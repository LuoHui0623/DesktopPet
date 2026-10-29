import sqlite3
from pathlib import Path

from infra.store.db import connect
from module.settings.contracts import AppSettingRecord, AppSettingRow
from service.store import upsert

SELECT_SQL = "SELECT key, value, updated_at FROM app_setting WHERE key = ?"
UPSERT_SQL = (
    "INSERT INTO app_setting (key, value, updated_at) VALUES (?, ?, ?) "
    "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
)


def get(conn: sqlite3.Connection, key: str) -> AppSettingRow | None:
    row = conn.execute(SELECT_SQL, (key,)).fetchone()
    return None if row is None else _row(row)


def put(conn: sqlite3.Connection, record: AppSettingRecord) -> None:
    upsert(conn, UPSERT_SQL, (record.key, record.value, record.updated_at))


def _row(row: sqlite3.Row | tuple) -> AppSettingRow:
    return AppSettingRow(key=row[0], value=row[1], updated_at=row[2])


class DbAppSettings:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def get(self, key: str) -> AppSettingRow | None:
        conn = connect(self._db_file)
        try:
            return get(conn, key)
        finally:
            conn.close()

    def put(self, record: AppSettingRecord) -> None:
        conn = connect(self._db_file)
        try:
            put(conn, record)
        finally:
            conn.close()
