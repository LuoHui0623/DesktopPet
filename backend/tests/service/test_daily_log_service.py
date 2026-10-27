from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.memory.daily_log_service import TEMPORARY_MODE_KEY, DailyLogService

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


def make_service(db_file: Path, now: datetime) -> DailyLogService:
    return DailyLogService(
        DbDailyLog(db_file),
        DbAppSettings(db_file),
        TZ,
        clock=lambda tz: now.astimezone(tz),
    )


def test_each_turn_writes_master_and_me_records(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    assert service.record_turn("你好", "你好呀") is True
    records = service.read_day("2026-10-25")
    assert [(r.role, r.content) for r in records] == [("master", "你好"), ("me", "你好呀")]
    for record in records:
        assert datetime.fromisoformat(record.created_at).tzinfo is not None


def test_date_follows_configured_timezone(db_file: Path):
    utc_late = datetime(2026, 10, 25, 17, 30, tzinfo=ZoneInfo("UTC"))
    service = make_service(db_file, utc_late)
    service.record_turn("跨日", "好")
    assert [r.date for r in service.read_day("2026-10-26")] == ["2026-10-26", "2026-10-26"]
    assert service.read_day("2026-10-25") == []


def test_temporary_mode_blocks_writes(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.set_temporary_mode(True)
    assert service.temporary_mode() is True
    assert service.record_turn("不要留痕", "好的") is False
    assert service.read_day("2026-10-25") == []


def test_temporary_mode_can_be_turned_off(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.set_temporary_mode(True)
    service.record_turn("一", "二")
    service.set_temporary_mode(False)
    assert service.record_turn("三", "四") is True
    assert [r.content for r in service.read_day("2026-10-25")] == ["三", "四"]


def test_temporary_mode_is_stored_in_app_setting(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.set_temporary_mode(True)
    assert DbAppSettings(db_file).get(TEMPORARY_MODE_KEY) == "true"
    assert make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ)).temporary_mode()


def test_default_is_not_temporary(db_file: Path):
    assert make_service(db_file, datetime(2026, 10, 25, tzinfo=TZ)).temporary_mode() is False


def test_read_day_rejects_invalid_date(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, tzinfo=TZ))
    with pytest.raises(ValueError):
        service.read_day("2026-13-01")


def test_search_uses_keyword_and_range(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.record_turn("喜欢美式咖啡", "记住了")
    assert [r.content for r in service.search("咖啡")] == ["喜欢美式咖啡"]
    assert service.search("咖啡", date_from="2026-10-26") == []
    assert service.search("", date_to="2026-10-25") != []
