import sqlite3
from collections.abc import Sequence
from pathlib import Path

from infra.store.db import connect, transaction
from module.memory.contracts import DailyLogRecord, DailyLogRow
from service.store import insert_many

COLUMNS = "id, date, role, content, created_at"
INSERT_SQL = "INSERT INTO daily_log (date, role, content, created_at) VALUES (?, ?, ?, ?)"


def insert(conn: sqlite3.Connection, records: Sequence[DailyLogRecord]) -> None:
    insert_many(
        conn,
        INSERT_SQL,
        [(record.date, record.role, record.content, record.created_at) for record in records],
    )


def by_date(conn: sqlite3.Connection, date: str) -> list[DailyLogRow]:
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM daily_log WHERE date = ? ORDER BY created_at, id",
        (date,),
    ).fetchall()
    return [_row(row) for row in rows]


def dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT date FROM daily_log ORDER BY date").fetchall()
    return [row[0] for row in rows]


def search(
    conn: sqlite3.Connection,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    newest_first: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> list[DailyLogRow]:
    clauses: list[str] = []
    params: list[str | int] = []
    if keyword:
        clauses.append("content LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(keyword))
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "DESC" if newest_first else "ASC"
    paging = ""
    if limit is not None:
        paging = "LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM daily_log {where} ORDER BY created_at {order}, id {order} {paging}",
        params,
    ).fetchall()
    return [_row(row) for row in rows]


def _like_pattern(keyword: str) -> str:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _row(row: sqlite3.Row | tuple) -> DailyLogRow:
    return DailyLogRow(id=row[0], date=row[1], role=row[2], content=row[3], created_at=row[4])


class DbDailyLog:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def append(self, records: Sequence[DailyLogRecord]) -> None:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                insert(conn, records)
        finally:
            conn.close()

    def by_date(self, date: str) -> list[DailyLogRow]:
        conn = connect(self._db_file)
        try:
            return by_date(conn, date)
        finally:
            conn.close()

    def dates(self) -> list[str]:
        conn = connect(self._db_file)
        try:
            return dates(conn)
        finally:
            conn.close()

    def search(
        self,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        newest_first: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[DailyLogRow]:
        conn = connect(self._db_file)
        try:
            return search(
                conn,
                keyword,
                date_from,
                date_to,
                newest_first=newest_first,
                limit=limit,
                offset=offset,
            )
        finally:
            conn.close()
