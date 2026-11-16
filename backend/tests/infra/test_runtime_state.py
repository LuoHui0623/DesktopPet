from datetime import datetime
from pathlib import Path

import pytest

from infra.store.db import connect
from infra.store.kv import DbRuntimeState, MemoryKeyValueStore, RuntimeStateStore
from infra.store.migrate import migrate


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


def test_missing_key_is_none(db_file: Path):
    assert RuntimeStateStore(db_file).get("missing") is None


def test_put_round_trips_value(db_file: Path):
    store = RuntimeStateStore(db_file)
    store.put("scheduler.sent", '{"date": "2026-11-16"}')
    assert store.get("scheduler.sent") == '{"date": "2026-11-16"}'


def test_put_overwrites_existing_value(db_file: Path):
    store = RuntimeStateStore(db_file)
    store.put("scheduler.sent", "a")
    store.put("scheduler.sent", "b")
    assert store.get("scheduler.sent") == "b"


def test_row_carries_timezone_aware_updated_at(db_file: Path):
    store = RuntimeStateStore(db_file)
    store.put("a", "1")
    row = DbRuntimeState(db_file).get("a")
    assert row is not None
    assert (row.key, row.value) == ("a", "1")
    assert datetime.fromisoformat(row.updated_at).tzinfo is not None


def test_old_table_name_is_gone_after_migration(db_file: Path):
    conn = connect(db_file)
    try:
        names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()
    assert "runtime_state" in names
    assert "app_setting" not in names


def test_memory_store_matches_the_database_contract():
    store = MemoryKeyValueStore()
    assert store.get("k") is None
    store.put("k", "v")
    assert store.get("k") == "v"
