import itertools
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.memory.daily_events import EventError
from module.memory.daily_log_service import DailyLogService
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

    assert [(r.event_time.date().isoformat(), r.role) for r in service.read_day("2026-10-25")] == [
        ("2026-10-25", "master")
    ]
    assert [(r.event_time.date().isoformat(), r.role) for r in service.read_day("2026-10-26")] == [
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


def test_proactive_reply_ignores_the_legacy_temporary_setting(db_file: Path):
    SettingsService(db_file).put("memory.temporary_mode", "true")
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    assert service.record_reply("照常留痕") is True
    assert [(r.role, r.content) for r in service.read_day("2026-10-25")] == [("me", "照常留痕")]


def test_date_follows_configured_timezone(db_file: Path):
    utc_late = datetime(2026, 10, 25, 17, 30, tzinfo=ZoneInfo("UTC"))
    service = make_service(db_file, utc_late)
    service.record_turn("跨日", "好")
    assert [r.event_time.date().isoformat() for r in service.read_day("2026-10-26")] == [
        "2026-10-26",
        "2026-10-26",
    ]
    assert service.read_day("2026-10-25") == []


def test_legacy_temporary_setting_does_not_block_turns(db_file: Path):
    SettingsService(db_file).put("memory.temporary_mode", "true")
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    assert service.record_turn("不要留痕", "照常回复") is True
    assert [(r.role, r.content) for r in service.read_day("2026-10-25")] == [
        ("master", "不要留痕"),
        ("me", "照常回复"),
    ]


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


def test_event_is_still_written_with_the_legacy_temporary_setting(db_file: Path):
    SettingsService(db_file).put("memory.temporary_mode", "true")
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    assert service.record_event("dispatch_failed", capability="记忆检索", reason="超时") is True
    assert [r.role for r in service.read_day("2026-10-25")] == ["event"]


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


def test_user_input_is_written_without_waiting_for_a_reply(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    assert service.record_user("只有输入") is True
    assert [(r.role, r.content) for r in service.read_day("2026-10-25")] == [("master", "只有输入")]


def test_master_assistant_and_me_are_written_independently(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.record_user("查一下")
    service.record_assistant("查到咖啡偏好")
    service.record_reply(["记下啦。", "还有茶。"])
    assert [(r.role, r.content) for r in service.read_day("2026-10-25")] == [
        ("master", "查一下"),
        ("assistant", "查到咖啡偏好"),
        ("me", "记下啦。"),
        ("me", "还有茶。"),
    ]


def test_read_day_uses_the_active_persona_by_default(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.record_user("芙芙的", persona_id="芙芙")
    service.record_user("小桌的", persona_id="小桌")
    SettingsService(db_file).put("persona.active", "小桌")
    assert [r.content for r in service.read_day("2026-10-25")] == ["小桌的"]
    assert [r.content for r in service.read_day("2026-10-25", persona_id="芙芙")] == ["芙芙的"]


def test_restore_excludes_events_and_other_personas_but_keeps_order(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.record_user("第一句", persona_id="芙芙")
    service.record_event("dispatch_failed", capability="记忆检索", reason="超时")
    service.record_assistant("查询结果", persona_id="芙芙")
    service.record_reply("第二句", persona_id="芙芙")
    service.record_user("别人的话", persona_id="小桌")

    entries = service.restore_day("2026-10-25", persona_id="芙芙")
    assert [(r.role, r.content) for r in entries] == [
        ("master", "第一句"),
        ("assistant", "查询结果"),
        ("me", "第二句"),
    ]


def test_new_records_follow_the_switched_persona(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.record_user("芙芙在")
    SettingsService(db_file).put("persona.active", "小桌")
    service.record_user("小桌在")
    assert service.active_persona() == "小桌"
    assert [r.content for r in service.read_day("2026-10-25")] == ["小桌在"]
    assert [r.content for r in service.read_day("2026-10-25", persona_id="芙芙")] == ["芙芙在"]


def test_recollection_is_written_once_per_persona_and_day(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    first = service.record_recollection("2026-10-24", "昨天聊了咖啡")
    assert first is not None
    assert first.role == "回忆"
    assert first.content == "昨天聊了咖啡"
    assert service.has_recollection("2026-10-24") is True

    again = service.record_recollection("2026-10-24", "重复生成")
    assert again is not None
    assert again.content == "昨天聊了咖啡"
    assert service.read_recollection("2026-10-24").content == "昨天聊了咖啡"


def test_recollection_is_read_per_persona_and_day(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.record_recollection("2026-10-24", "芙芙的回忆", persona_id="芙芙")
    service.record_recollection("2026-10-24", "小桌的回忆", persona_id="小桌")

    assert service.read_recollection("2026-10-24", persona_id="芙芙").content == "芙芙的回忆"
    assert service.read_recollection("2026-10-24", persona_id="小桌").content == "小桌的回忆"
    assert service.read_recollection("2026-10-23", persona_id="芙芙") is None


def test_recollection_keeps_the_generation_moment_hours(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    record = service.record_recollection("2026-10-24", "昨天聊了咖啡")
    assert record.event_time.date().isoformat() == "2026-10-24"
    assert record.created_at == datetime(2026, 10, 25, 9, 0, tzinfo=TZ)


def test_recollection_is_hidden_from_the_chat_timeline(db_file: Path):
    service = make_service(db_file, datetime(2026, 10, 25, 9, 0, tzinfo=TZ))
    service.record_user("第一句", persona_id="芙芙")
    service.record_recollection("2026-10-25", "当天总结")
    service.record_reply("第二句", persona_id="芙芙")

    assert [(r.role, r.content) for r in service.restore_day("2026-10-25", persona_id="芙芙")] == [
        ("master", "第一句"),
        ("me", "第二句"),
    ]
