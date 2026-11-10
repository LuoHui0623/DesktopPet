from pathlib import Path

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.persona import repository as persona_version
from module.persona.repository import DbPersonaVersions

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
