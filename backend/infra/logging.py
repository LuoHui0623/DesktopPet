import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "backend.log"
FILE_HANDLER_NAME = "pet-file"
FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


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
    for existing in list(root.handlers):
        if existing.get_name() == FILE_HANDLER_NAME:
            root.removeHandler(existing)
            existing.close()
    root.addHandler(handler)
    root.setLevel(level)
    return log_file
