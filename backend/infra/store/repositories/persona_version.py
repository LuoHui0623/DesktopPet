import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaVersion:
    version: int
    content: str
    created_at: str


def latest(conn: sqlite3.Connection) -> PersonaVersion | None:
    row = conn.execute(
        "SELECT version, content, created_at FROM persona_version ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return PersonaVersion(version=row[0], content=row[1], created_at=row[2])


def append(conn: sqlite3.Connection, content: str, created_at: str) -> PersonaVersion:
    row = conn.execute(
        "INSERT INTO persona_version (version, content, created_at) "
        "SELECT COALESCE(MAX(version), 0) + 1, ?, ? FROM persona_version "
        "RETURNING version",
        (content, created_at),
    ).fetchone()
    return PersonaVersion(version=row[0], content=content, created_at=created_at)
