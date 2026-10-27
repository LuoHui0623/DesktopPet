from pathlib import Path

from infra.store.db import connect, transaction
from infra.store.repositories import persona_version
from infra.store.repositories.persona_version import PersonaVersion


class DbPersonaVersions:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def latest(self, persona_id: str) -> PersonaVersion | None:
        conn = connect(self._db_file)
        try:
            return persona_version.latest(conn, persona_id)
        finally:
            conn.close()

    def append(self, persona_id: str, content: str, created_at: str) -> PersonaVersion:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                return persona_version.append(conn, persona_id, content, created_at)
        finally:
            conn.close()
