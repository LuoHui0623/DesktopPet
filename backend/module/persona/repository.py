import sqlite3
from pathlib import Path

from infra.store.db import connect, transaction
from module.persona.contracts import PersonaVersion


def latest(conn: sqlite3.Connection, persona_id: str) -> PersonaVersion | None:
    row = conn.execute(
        "SELECT persona_id, version, content, created_at FROM persona_version "
        "WHERE persona_id = ? ORDER BY version DESC LIMIT 1",
        (persona_id,),
    ).fetchone()
    if row is None:
        return None
    return PersonaVersion(persona_id=row[0], version=row[1], content=row[2], created_at=row[3])


def append(
    conn: sqlite3.Connection, persona_id: str, content: str, created_at: str
) -> PersonaVersion:
    row = conn.execute(
        "INSERT INTO persona_version (persona_id, version, content, created_at) "
        "SELECT ?, COALESCE(MAX(version), 0) + 1, ?, ? FROM persona_version "
        "WHERE persona_id = ? RETURNING version",
        (persona_id, content, created_at, persona_id),
    ).fetchone()
    return PersonaVersion(
        persona_id=persona_id, version=row[0], content=content, created_at=created_at
    )


class DbPersonaVersions:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def latest(self, persona_id: str) -> PersonaVersion | None:
        conn = connect(self._db_file)
        try:
            return latest(conn, persona_id)
        finally:
            conn.close()

    def append(self, persona_id: str, content: str, created_at: str) -> PersonaVersion:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                return append(conn, persona_id, content, created_at)
        finally:
            conn.close()
