from pathlib import Path

import pytest

from constants.agent import DEFAULT_PERSONA_ID
from constants.path import MIGRATIONS_DIR
from infra.store.db import connect
from infra.store.migrate import migrate
from infra.store.persona_versions import DbPersonaVersions
from infra.store.repositories import persona_version

CREATED_AT = "2026-10-26T09:00:00+08:00"


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "pet.db")
    migrate(connection)
    yield connection
    connection.close()


def test_latest_is_none_when_empty(conn):
    assert persona_version.latest(conn, "小桌") is None


def test_first_append_is_version_one_for_that_card(conn):
    written = persona_version.append(conn, "小桌", '{"role": 1}', CREATED_AT)
    assert (written.persona_id, written.version) == ("小桌", 1)
    assert persona_version.latest(conn, "小桌") == written


def test_versions_are_counted_per_card(conn):
    persona_version.append(conn, "小桌", '{"role": 1}', CREATED_AT)
    persona_version.append(conn, "小宠", '{"role": 9}', CREATED_AT)
    second = persona_version.append(conn, "小桌", '{"role": 2}', CREATED_AT)
    assert second.version == 2
    assert persona_version.latest(conn, "小宠").version == 1
    rows = conn.execute(
        "SELECT persona_id, version FROM persona_version ORDER BY persona_id, version"
    ).fetchall()
    assert [tuple(row) for row in rows] == [("小宠", 1), ("小桌", 1), ("小桌", 2)]


def test_db_store_wraps_repository_with_transactions(tmp_path: Path):
    connection = connect(tmp_path / "pet.db")
    migrate(connection)
    connection.close()
    store = DbPersonaVersions(tmp_path / "pet.db")
    assert store.latest("小桌") is None
    assert store.append("小桌", "{}", CREATED_AT).version == 1
    assert store.latest("小桌").version == 1


def test_migration_keeps_existing_versions_under_default_card(tmp_path: Path):
    connection = connect(tmp_path / "old.db")
    try:
        for name in ("0001_init.sql", "0002_log_record.sql", "0003_agent.sql"):
            connection.executescript((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO persona_version (version, content, created_at) VALUES (1, '{}', ?)",
            (CREATED_AT,),
        )
        connection.executescript(
            (MIGRATIONS_DIR / "0004_persona_card.sql").read_text(encoding="utf-8")
        )
        row = connection.execute(
            "SELECT persona_id, version, content FROM persona_version"
        ).fetchone()
        assert tuple(row) == (DEFAULT_PERSONA_ID, 1, "{}")
    finally:
        connection.close()
