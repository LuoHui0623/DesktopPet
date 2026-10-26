import sqlite3

from config.persona import PersonaConfig
from infra.store.repositories import persona_version
from infra.store.repositories.persona_version import PersonaVersion


def sync_persona_version(
    conn: sqlite3.Connection, persona: PersonaConfig, created_at: str
) -> PersonaVersion:
    content = persona.to_normalized_json()
    current = persona_version.latest(conn)
    if current is not None and current.content == content:
        return current
    return persona_version.append(conn, content, created_at)
