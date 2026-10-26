from pathlib import Path

import pytest

from config.persona import PersonaConfig
from infra.store.db import connect
from infra.store.migrate import migrate
from service.persona.persona_service import sync_persona_version

CREATED_AT = "2026-10-26T09:00:00+08:00"


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "pet.db")
    migrate(connection)
    yield connection
    connection.close()


def persona(name: str, personality: tuple[str, ...] = ("温和", "好奇")) -> PersonaConfig:
    return PersonaConfig(content={"role": {"name": name, "personality": personality}})


def count_versions(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM persona_version").fetchone()[0]


def test_first_sync_writes_version_one(conn):
    written = sync_persona_version(conn, persona("小桌"), CREATED_AT)
    assert written.version == 1
    assert count_versions(conn) == 1


def test_unchanged_content_adds_no_version(conn):
    sync_persona_version(conn, persona("小桌"), CREATED_AT)
    again = sync_persona_version(conn, persona("小桌"), CREATED_AT)
    assert again.version == 1
    assert count_versions(conn) == 1


def test_list_order_change_adds_no_version(conn):
    sync_persona_version(conn, persona("小桌", ("温和", "好奇")), CREATED_AT)
    sync_persona_version(conn, persona("小桌", ("好奇", "温和")), CREATED_AT)
    assert count_versions(conn) == 1


def test_changed_content_increments_and_keeps_old_version(conn):
    sync_persona_version(conn, persona("小桌"), CREATED_AT)
    changed = sync_persona_version(conn, persona("小宠"), CREATED_AT)
    assert changed.version == 2
    assert count_versions(conn) == 2
    old = conn.execute("SELECT content FROM persona_version WHERE version = 1").fetchone()
    assert "小桌" in old[0]
