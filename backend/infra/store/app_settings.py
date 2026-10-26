from datetime import datetime
from pathlib import Path

from infra.store.db import connect
from infra.store.repositories import app_setting


class DbAppSettings:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def get(self, key: str) -> str | None:
        conn = connect(self._db_file)
        try:
            return app_setting.get(conn, key)
        finally:
            conn.close()

    def put(self, key: str, value: str) -> None:
        updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        conn = connect(self._db_file)
        try:
            app_setting.put(conn, key, value, updated_at)
        finally:
            conn.close()
