import logging
import sqlite3
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

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
LogRow = tuple[str, str, str, str, str | None]


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
    queue_size: int = 20,
    max_wait_seconds: float = 30.0,
    max_ready_batches: int = 50,
) -> "DbLogHandler":
    handler = DbLogHandler(
        db_file,
        queue_size=queue_size,
        max_wait_seconds=max_wait_seconds,
        max_ready_batches=max_ready_batches,
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
    """把日志批量写入 log_record 表。

    记录先进入当前批次（pending）。批次满 queue_size 条，或自批次第一条记录起
    超过 max_wait_seconds 秒，批次即封口，移入就绪队列（ready）。后台线程逐批写库，
    生产方（emit）始终只追加记录，不等待写库。

    就绪队列最多保留 max_ready_batches 个批次；已满时，需要封口的新记录被丢弃并计数（dropped）。
    close() 会封口并写完所有批次后返回；关闭之后的记录被忽略。
    """

    def __init__(
        self,
        db_file: Path,
        *,
        queue_size: int = 20,
        max_wait_seconds: float = 30.0,
        max_ready_batches: int = 50,
    ) -> None:
        if queue_size < 1:
            raise ValueError("queue_size 必须大于 0")
        if max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds 必须大于 0")
        if max_ready_batches < 1:
            raise ValueError("max_ready_batches 必须大于 0")
        super().__init__()
        self._db_file = db_file
        self._queue_size = queue_size
        self._max_wait_seconds = max_wait_seconds
        self._max_ready_batches = max_ready_batches
        self._cond = threading.Condition()
        self._pending: list[LogRow] = []
        self._deadline: float | None = None
        self._ready: deque[list[LogRow]] = deque()
        self._dropped = 0
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="pet-log-writer", daemon=True)
        self._thread.start()

    @property
    def dropped(self) -> int:
        with self._cond:
            return self._dropped

    def emit(self, record: logging.LogRecord) -> None:
        try:
            row = self._row(record)
        except Exception:
            self.handleError(record)
            return
        with self._cond:
            if self._closing:
                return
            if (
                len(self._pending) + 1 >= self._queue_size
                and len(self._ready) >= self._max_ready_batches
            ):
                self._dropped += 1
                return
            if not self._pending:
                self._deadline = time.monotonic() + self._max_wait_seconds
            self._pending.append(row)
            if len(self._pending) >= self._queue_size:
                self._seal_pending()
            self._cond.notify()

    def close(self) -> None:
        with self._cond:
            if not self._closing:
                self._closing = True
                self._cond.notify_all()
        self._thread.join()
        super().close()

    def _seal_pending(self) -> None:
        self._ready.append(self._pending)
        self._pending = []
        self._deadline = None

    def _next_batch(self) -> list[LogRow] | None:
        with self._cond:
            while True:
                if self._pending and (self._closing or self._deadline_passed()):
                    self._seal_pending()
                if self._ready:
                    return self._ready.popleft()
                if self._closing:
                    return None
                self._cond.wait(self._seconds_until_deadline())

    def _deadline_passed(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def _seconds_until_deadline(self) -> float | None:
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - time.monotonic())

    def _row(self, record: logging.LogRecord) -> LogRow:
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
        try:
            while True:
                batch = self._next_batch()
                if batch is None:
                    break
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

    def _write(self, conn: sqlite3.Connection | None, batch: list[LogRow]) -> None:
        if conn is None:
            return
        try:
            with transaction(conn):
                conn.executemany(INSERT_LOG_SQL, batch)
        except Exception as exc:
            print(f"日志写入数据库失败：{exc}", file=sys.stderr)
