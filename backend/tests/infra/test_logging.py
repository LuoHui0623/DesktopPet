import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from infra.logging import FILE_HANDLER_NAME, LOG_FILE_NAME, setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def file_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if h.get_name() == FILE_HANDLER_NAME]


def test_creates_log_file_in_given_directory(tmp_path: Path):
    log_file = setup_logging("INFO", tmp_path / "logs")
    assert log_file == tmp_path / "logs" / LOG_FILE_NAME
    assert log_file.parent.is_dir()


def test_writes_messages_at_or_above_level(tmp_path: Path):
    log_file = setup_logging("INFO", tmp_path)
    logger = logging.getLogger("pet.test")
    logger.debug("hidden-debug")
    logger.info("visible-info")
    logger.error("visible-error")
    content = log_file.read_text(encoding="utf-8")
    assert "visible-info" in content
    assert "visible-error" in content
    assert "hidden-debug" not in content


def test_level_is_applied_to_root(tmp_path: Path):
    setup_logging("ERROR", tmp_path)
    assert logging.getLogger().level == logging.ERROR


def test_repeated_setup_does_not_duplicate_handlers(tmp_path: Path):
    setup_logging("INFO", tmp_path)
    log_file = setup_logging("INFO", tmp_path)
    assert len(file_handlers()) == 1
    logging.getLogger("pet.test").info("once")
    assert log_file.read_text(encoding="utf-8").count("once") == 1


def test_unicode_messages_are_written_as_utf8(tmp_path: Path):
    log_file = setup_logging("INFO", tmp_path)
    logging.getLogger("pet.test").info("后端已启动")
    assert "后端已启动" in log_file.read_text(encoding="utf-8")
