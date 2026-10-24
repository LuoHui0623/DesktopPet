import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from infra.logging import DB_HANDLER_NAME, DbLogHandler, attach_db_logging
from infra.store.db import connect
from infra.store.migrate import migrate

ISO_MILLIS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d$")


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def make_handler(db_file: Path) -> Iterator[Callable[..., DbLogHandler]]:
    created: list[DbLogHandler] = []

    def factory(**options) -> DbLogHandler:
        handler = DbLogHandler(db_file, **options)
        created.append(handler)
        return handler

    yield factory
    for handler in created:
        handler.close()


@pytest.fixture
def test_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("pet.dbtest")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)


def rows(db_file: Path) -> list[tuple]:
    conn = connect(db_file)
    try:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT created_at, level, logger, message, exc_text FROM log_record ORDER BY id"
            )
        ]
    finally:
        conn.close()


def count(db_file: Path) -> int:
    return len(rows(db_file))


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_records_are_written_on_close(db_file, make_handler, test_logger):
    handler = make_handler()
    test_logger.addHandler(handler)
    test_logger.info("hello %s", "world")
    test_logger.warning("careful")
    handler.close()

    stored = rows(db_file)
    assert [(row[1], row[2], row[3]) for row in stored] == [
        ("INFO", "pet.dbtest", "hello world"),
        ("WARNING", "pet.dbtest", "careful"),
    ]


def test_timestamp_is_iso_with_offset_and_milliseconds(db_file, make_handler, test_logger):
    handler = make_handler()
    test_logger.addHandler(handler)
    test_logger.info("time check")
    handler.close()
    assert ISO_MILLIS.match(rows(db_file)[0][0])


def test_exception_text_is_stored(db_file, make_handler, test_logger):
    handler = make_handler()
    test_logger.addHandler(handler)
    try:
        raise ValueError("boom")
    except ValueError:
        test_logger.exception("failed")
    handler.close()
    exc_text = rows(db_file)[0][4]
    assert "ValueError: boom" in exc_text


def test_flushes_by_interval_without_close(db_file, make_handler, test_logger):
    handler = make_handler(flush_interval=0.05)
    test_logger.addHandler(handler)
    test_logger.info("eventually")
    assert wait_for(lambda: count(db_file) == 1)


def test_flushes_when_batch_is_full(db_file, make_handler, test_logger):
    handler = make_handler(batch_size=5, flush_interval=60)
    test_logger.addHandler(handler)
    for index in range(5):
        test_logger.info("batch %d", index)
    assert wait_for(lambda: count(db_file) == 5)


def test_close_drains_every_queued_record(db_file, make_handler, test_logger):
    handler = make_handler(batch_size=100, flush_interval=60)
    test_logger.addHandler(handler)
    for index in range(250):
        test_logger.info("row %d", index)
    handler.close()
    assert count(db_file) == 250


def test_full_queue_drops_without_blocking(monkeypatch, db_file, make_handler, test_logger):
    gate = threading.Event()

    def blocked_write(self, conn, batch):
        gate.wait(10)

    monkeypatch.setattr(DbLogHandler, "_write", blocked_write)
    handler = make_handler(max_queue=2, batch_size=1, flush_interval=60)
    test_logger.addHandler(handler)

    started = time.monotonic()
    for index in range(10):
        test_logger.info("flood %d", index)
    elapsed = time.monotonic() - started

    assert elapsed < 2
    assert handler.dropped >= 7
    gate.set()
    handler.close()


def test_records_after_close_are_ignored(db_file, make_handler, test_logger):
    handler = make_handler()
    test_logger.addHandler(handler)
    handler.close()
    test_logger.info("too late")
    assert count(db_file) == 0


def test_unwritable_database_does_not_break_logging(tmp_path: Path, test_logger):
    handler = DbLogHandler(tmp_path)
    test_logger.addHandler(handler)
    test_logger.info("still fine")
    handler.close()


def test_attach_replaces_previous_db_handler(db_file, test_logger):
    root = logging.getLogger()
    first = attach_db_logging(db_file)
    second = attach_db_logging(db_file)
    try:
        handlers = [h for h in root.handlers if h.get_name() == DB_HANDLER_NAME]
        assert handlers == [second]
        assert first not in root.handlers
    finally:
        root.removeHandler(second)
        second.close()
        first.close()
