import itertools
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.memory.daily_events import EventError
from module.memory.daily_log_service import TEMPORARY_MODE_KEY, DailyLogService
from module.memory.repository import DbDailyLog
from module.settings.settings_service import SettingsService

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
        SettingsService(db_file),
        TZ,
        clock=lambda tz: now.astimezone(tz),
    )


def test_each_turn_writes_master_and_me_records(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    assert service.record_turn("你好", "你好呀") is True
    records = service.read_day("2026-10-25")
    assert [(r.role, r.content) for r in records] == [("master", "你好"), ("me", "你好呀")]
    for record in records:
        assert record.created_at.tzinfo is not None


def test_several_utterances_are_written_as_ordered_me_records(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    assert service.record_turn("你好", ["第一句。", "第二句！", "第三句。"]) is True
    records = service.read_day("2026-10-25")
    assert [(r.role, r.content) for r in records] == [
        ("master", "你好"),
        ("me", "第一句。"),
        ("me", "第二句！"),
        ("me", "第三句。"),
    ]


def test_input_and_reply_use_their_own_sampled_times(db_file: Path):
    received = datetime(2026, 10, 25, 23, 59, 50, tzinfo=TZ)
    replied = datetime(2026, 10, 26, 0, 0, 10, tzinfo=TZ)
    service = make_service(db_file, received)

    assert service.record_turn("晚安", ["晚安呀"], received_at=received, replied_at=replied) is True

    assert [(r.day.isoformat(), r.role) for r in service.read_day("2026-10-25")] == [
        ("2026-10-25", "master")
    ]
    assert [(r.day.isoformat(), r.role) for r in service.read_day("2026-10-26")] == [
        ("2026-10-26", "me")
    ]


def test_master_and_reply_times_are_sampled_separately_by_default(db_file: Path):
    base = datetime(2026, 10, 25, 9, 0, 0, tzinfo=TZ)
    ticks = itertools.count()
    service = DailyLogService(
        DbDailyLog(db_file),
        SettingsService(db_file),
        TZ,
        clock=lambda tz: (base + timedelta(seconds=next(ticks))).astimezone(tz),
    )

    assert service.record_turn("你好", ["你好呀"]) is True

    records = service.read_day("2026-10-25")
    assert records[0].role == "master"
    assert records[1].role == "me"
    assert records[0].created_at < records[1].created_at


def test_proactive_reply_writes_only_one_me_record(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    assert service.record_reply("今天也辛苦啦") is True
    records = service.read_day("2026-10-25")
    assert [(r.role, r.content) for r in records] == [("me", "今天也辛苦啦")]


def test_proactive_reply_is_blocked_in_temporary_mode(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.set_temporary_mode(True)
    assert service.record_reply("不要留痕") is False
    assert service.read_day("2026-10-25") == []


def test_date_follows_configured_timezone(db_file: Path):
    utc_late = datetime(2026, 10, 25, 17, 30, tzinfo=ZoneInfo("UTC"))
    service = make_service(db_file, utc_late)
    service.record_turn("跨日", "好")
    assert [r.day.isoformat() for r in service.read_day("2026-10-26")] == [
        "2026-10-26",
        "2026-10-26",
    ]
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
    assert SettingsService(db_file).get(TEMPORARY_MODE_KEY) == "true"
    assert make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ)).temporary_mode()


def test_default_is_not_temporary(db_file: Path):
    assert make_service(db_file, datetime(2026, 10, 25, tzinfo=TZ)).temporary_mode() is False


def test_session_boundary_is_written_as_structured_event(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    assert service.record_session_boundary("小桌") is True
    [record] = service.read_day("2026-10-25")
    assert record.role == "event"
    assert json.loads(record.content) == {"type": "session_boundary", "persona": "小桌"}


def test_invalid_event_is_rejected_and_nothing_is_written(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    with pytest.raises(EventError):
        service.record_event("dispatch_failed", capability="记忆检索")
    assert service.read_day("2026-10-25") == []


def test_event_is_not_written_in_temporary_mode(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.set_temporary_mode(True)
    assert service.record_event("dispatch_failed", capability="记忆检索", reason="超时") is False
    assert service.read_day("2026-10-25") == []


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
