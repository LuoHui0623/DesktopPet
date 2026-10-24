import logging
import queue
import sqlite3
import sys
import threading
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from infra.store.db import connect, transaction

LOG_FILE_NAME = "backend.log"
FILE_HANDLER_NAME = "pet-file"
DB_HANDLER_NAME = "pet-db"
FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

INSERT_LOG_SQL = (
    "INSERT INTO log_record (created_at, level, logger, message, exc_text) VALUES (?, ?, ?, ?, ?)"
)
_STOP = object()


def setup_logging(level: str, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILE_NAME

    handler = RotatingFileHandler(
        log_file,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.set_name(FILE_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(FORMAT))

    root = logging.getLogger()
    _replace_handler(root, FILE_HANDLER_NAME, handler)
    root.setLevel(level)
    return log_file


def attach_db_logging(
    db_file: Path,
    *,
    max_queue: int = 10000,
    batch_size: int = 100,
    flush_interval: float = 1.0,
) -> "DbLogHandler":
    handler = DbLogHandler(
        db_file,
        max_queue=max_queue,
        batch_size=batch_size,
        flush_interval=flush_interval,
    )
    handler.set_name(DB_HANDLER_NAME)
    _replace_handler(logging.getLogger(), DB_HANDLER_NAME, handler)
    return handler


def _replace_handler(logger: logging.Logger, name: str, handler: logging.Handler) -> None:
    for existing in list(logger.handlers):
        if existing.get_name() == name:
            logger.removeHandler(existing)
            existing.close()
    logger.addHandler(handler)


class DbLogHandler(logging.Handler):
    """把日志记录放入有界队列，由后台线程批量写入 log_record 表。

    emit 只做格式化与入队，不访问数据库。队列满时丢弃新记录并计数，不阻塞调用方。
    close 会写完队列中剩余的记录后再返回。
    """

    def __init__(
        self,
        db_file: Path,
        *,
        max_queue: int = 10000,
        batch_size: int = 100,
        flush_interval: float = 1.0,
    ) -> None:
        super().__init__()
        self._db_file = db_file
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue)
        self._counter_lock = threading.Lock()
        self._dropped = 0
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="pet-log-writer", daemon=True)
        self._thread.start()

    @property
    def dropped(self) -> int:
        with self._counter_lock:
            return self._dropped

    def emit(self, record: logging.LogRecord) -> None:
        if self._closing:
            return
        try:
            row = self._row(record)
        except Exception:
            self.handleError(record)
            return
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            with self._counter_lock:
                self._dropped += 1

    def close(self) -> None:
        if not self._closing:
            self._closing = True
            self._queue.put(_STOP)
            self._thread.join()
        super().close()

    def _row(self, record: logging.LogRecord) -> tuple[str, str, str, str, str | None]:
        created_at = datetime.fromtimestamp(record.created).astimezone()
        exc_text = (
            "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None
        )
        return (
            created_at.isoformat(timespec="milliseconds"),
            record.levelname,
            record.name,
            record.getMessage(),
            exc_text,
        )

    def _run(self) -> None:
        conn = self._open()
        batch: list[tuple[str, str, str, str, str | None]] = []
        try:
            while True:
                try:
                    item = self._queue.get(timeout=self._flush_interval)
                except queue.Empty:
                    item = None
                if item is _STOP:
                    break
                if item is not None:
                    batch.append(item)
                if batch and (item is None or len(batch) >= self._batch_size):
                    self._write(conn, batch)
                    batch = []
            if batch:
                self._write(conn, batch)
        finally:
            if conn is not None:
                conn.close()

    def _open(self) -> sqlite3.Connection | None:
        try:
            return connect(self._db_file)
        except Exception as exc:
            print(f"日志数据库连接失败，日志将不写入数据库：{exc}", file=sys.stderr)
            return None

    def _write(self, conn: sqlite3.Connection | None, batch: list[Any]) -> None:
        if conn is None:
            return
        try:
            with transaction(conn):
                conn.executemany(INSERT_LOG_SQL, batch)
        except Exception as exc:
            print(f"日志写入数据库失败：{exc}", file=sys.stderr)
