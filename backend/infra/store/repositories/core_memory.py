import sqlite3
from collections.abc import Sequence


def ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT memory_id FROM core_memory ORDER BY sort_order, memory_id"
    ).fetchall()
    return [row[0] for row in rows]


def insert(conn: sqlite3.Connection, memory_id: str, sort_order: int) -> None:
    conn.execute(
        "INSERT INTO core_memory (memory_id, sort_order) VALUES (?, ?)",
        (memory_id, sort_order),
    )


def remove(conn: sqlite3.Connection, memory_id: str) -> bool:
    cursor = conn.execute("DELETE FROM core_memory WHERE memory_id = ?", (memory_id,))
    return cursor.rowcount > 0


def rewrite(conn: sqlite3.Connection, ordered_ids: Sequence[str]) -> None:
    conn.execute("DELETE FROM core_memory")
    for position, memory_id in enumerate(ordered_ids, start=1):
        insert(conn, memory_id, position)
