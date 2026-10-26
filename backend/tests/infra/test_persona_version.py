from pathlib import Path

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from infra.store.repositories import persona_version

CREATED_AT = "2026-10-26T09:00:00+08:00"


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "pet.db")
    migrate(connection)
    yield connection
    connection.close()


def test_latest_is_none_when_empty(conn):
    assert persona_version.latest(conn) is None


def test_first_append_is_version_one(conn):
    written = persona_version.append(conn, '{"role": 1}', CREATED_AT)
    assert written.version == 1
    assert persona_version.latest(conn) == written


def test_append_increments_and_keeps_old_versions(conn):
    persona_version.append(conn, '{"role": 1}', CREATED_AT)
    second = persona_version.append(conn, '{"role": 2}', CREATED_AT)
    assert second.version == 2
    assert persona_version.latest(conn) == second
    rows = conn.execute("SELECT version, content FROM persona_version ORDER BY version").fetchall()
    assert [tuple(row) for row in rows] == [(1, '{"role": 1}'), (2, '{"role": 2}')]
