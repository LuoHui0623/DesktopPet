import sqlite3
from dataclasses import dataclass

COLUMNS = "id, date, role, content, created_at"


@dataclass(frozen=True)
class DailyLogRecord:
    id: int
    date: str
    role: str
    content: str
    created_at: str


def insert(conn: sqlite3.Connection, date: str, role: str, content: str, created_at: str) -> int:
    cursor = conn.execute(
        "INSERT INTO daily_log (date, role, content, created_at) VALUES (?, ?, ?, ?)",
        (date, role, content, created_at),
    )
    return cursor.lastrowid


def by_date(conn: sqlite3.Connection, date: str) -> list[DailyLogRecord]:
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM daily_log WHERE date = ? ORDER BY created_at, id",
        (date,),
    ).fetchall()
    return [_record(row) for row in rows]


def search(
    conn: sqlite3.Connection,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    newest_first: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> list[DailyLogRecord]:
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
    return [_record(row) for row in rows]


def _like_pattern(keyword: str) -> str:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _record(row: sqlite3.Row | tuple) -> DailyLogRecord:
    return DailyLogRecord(id=row[0], date=row[1], role=row[2], content=row[3], created_at=row[4])
