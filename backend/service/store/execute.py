import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any


def insert_many(conn: sqlite3.Connection, sql: str, rows: Iterable[Sequence[Any]]) -> int:
    return conn.executemany(sql, rows).rowcount


def upsert(conn: sqlite3.Connection, sql: str, params: Sequence[Any]) -> int:
    return conn.execute(sql, params).rowcount
