import sqlite3
from collections.abc import Sequence
from pathlib import Path

from infra.store.db import connect, transaction
from module.memory.contracts import DailyLogRecord, DailyLogRow
from service.store import insert_many

COLUMNS = "id, event_time, role, content, persona_id, created_at"
INSERT_SQL = (
    "INSERT INTO daily_log (event_time, role, content, persona_id, created_at) "
    "VALUES (?, ?, ?, ?, ?)"
)
DAY = "substr(event_time, 1, 10)"


def insert(conn: sqlite3.Connection, records: Sequence[DailyLogRecord]) -> None:
    insert_many(
        conn,
        INSERT_SQL,
        [
            (
                record.event_time,
                record.role,
                record.content,
                record.persona_id,
                record.created_at,
            )
            for record in records
        ],
    )


def by_date(
    conn: sqlite3.Connection, day: str, *, persona_id: str | None = None
) -> list[DailyLogRow]:
    if persona_id is None:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM daily_log WHERE {DAY} = ? ORDER BY created_at, id",
            (day,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM daily_log WHERE {DAY} = ? AND persona_id = ? "
            "ORDER BY created_at, id",
            (day, persona_id),
        ).fetchall()
    return [_row(row) for row in rows]


def dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(f"SELECT DISTINCT {DAY} AS day FROM daily_log ORDER BY day").fetchall()
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
    persona_id: str | None = None,
) -> list[DailyLogRow]:
    clauses: list[str] = []
    params: list[str | int] = []
    if keyword:
        clauses.append("content LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(keyword))
    if date_from:
        clauses.append(f"{DAY} >= ?")
        params.append(date_from)
    if date_to:
        clauses.append(f"{DAY} <= ?")
        params.append(date_to)
    if persona_id:
        clauses.append("persona_id = ?")
        params.append(persona_id)
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
    return DailyLogRow(
        id=row[0],
        event_time=row[1],
        role=row[2],
        content=row[3],
        persona_id=row[4],
        created_at=row[5],
    )


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

    def by_date(self, day: str, *, persona_id: str | None = None) -> list[DailyLogRow]:
        conn = connect(self._db_file)
        try:
            return by_date(conn, day, persona_id=persona_id)
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
        persona_id: str | None = None,
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
                persona_id=persona_id,
            )
        finally:
            conn.close()
