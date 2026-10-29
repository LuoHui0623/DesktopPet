from datetime import datetime
from pathlib import Path

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.settings.repository import DbAppSettings
from module.settings.settings_service import SettingsService


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


def test_missing_key_is_none(db_file: Path):
    assert SettingsService(db_file).get("missing") is None


def test_put_round_trips_value(db_file: Path):
    service = SettingsService(db_file)
    service.put("persona.active", "小桌")
    assert service.get("persona.active") == "小桌"


def test_put_overwrites_existing_value(db_file: Path):
    service = SettingsService(db_file)
    service.put("scheduler.dnd_start", "22:30")
    service.put("scheduler.dnd_start", "23:00")
    assert service.get("scheduler.dnd_start") == "23:00"


def test_row_carries_timezone_aware_updated_at(db_file: Path):
    service = SettingsService(db_file)
    service.put("a", "1")
    row = DbAppSettings(db_file).get("a")
    assert row is not None
    assert (row.key, row.value) == ("a", "1")
    assert datetime.fromisoformat(row.updated_at).tzinfo is not None
