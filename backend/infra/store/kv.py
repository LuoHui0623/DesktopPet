"""`runtime_state` 表的键值门面：行契约、读写原语与内存实现。

用户配置在 `T6` 之后只存在于 `data/settings.json`（由 `app/settings/document.py`
拥有）。本表改名为 `runtime_state`，只承载无法进快照的运行时台账——当前仅
`scheduler.sent`（每日重置的发送计数）。

`MemoryKeyValueStore` 给不落库的单元测试与无库场景使用，接口与数据库实现一致。
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from infra.store.db import connect
from infra.store.execute import upsert

TABLE = "runtime_state"
SELECT_SQL = f"SELECT key, value, updated_at FROM {TABLE} WHERE key = ?"
UPSERT_SQL = (
    f"INSERT INTO {TABLE} (key, value, updated_at) VALUES (?, ?, ?) "
    "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
)


@dataclass(frozen=True)
class RuntimeStateRow:
    key: str
    value: str
    updated_at: str


@dataclass(frozen=True)
class RuntimeStateRecord:
    key: str
    value: str
    updated_at: str


def get(conn: sqlite3.Connection, key: str) -> RuntimeStateRow | None:
    row = conn.execute(SELECT_SQL, (key,)).fetchone()
    return None if row is None else _row(row)


def put(conn: sqlite3.Connection, record: RuntimeStateRecord) -> None:
    upsert(conn, UPSERT_SQL, (record.key, record.value, record.updated_at))


def _row(row: sqlite3.Row | tuple) -> RuntimeStateRow:
    return RuntimeStateRow(key=row[0], value=row[1], updated_at=row[2])


class DbRuntimeState:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def get(self, key: str) -> RuntimeStateRow | None:
        conn = connect(self._db_file)
        try:
            return get(conn, key)
        finally:
            conn.close()

    def put(self, record: RuntimeStateRecord) -> None:
        conn = connect(self._db_file)
        try:
            put(conn, record)
        finally:
            conn.close()


class RuntimeStateStore:
    """运行时台账的读写门面；不是配置存储。"""

    def __init__(self, db_file: Path) -> None:
        self._rows = DbRuntimeState(db_file)

    def get(self, key: str) -> str | None:
        row = self._rows.get(key)
        return None if row is None else row.value

    def put(self, key: str, value: str) -> None:
        updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._rows.put(RuntimeStateRecord(key=key, value=value, updated_at=updated_at))


class MemoryKeyValueStore:
    """内存实现：不落库，用于单测与无需持久化的装配。"""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values: dict[str, str] = dict(values or {})

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def put(self, key: str, value: str) -> None:
        self._values[key] = value
