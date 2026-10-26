import sqlite3


def get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_setting WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def put(conn: sqlite3.Connection, key: str, value: str, updated_at: str) -> None:
    conn.execute(
        "INSERT INTO app_setting (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, updated_at),
    )
