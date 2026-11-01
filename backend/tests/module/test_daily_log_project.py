from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from module.memory.contracts import DailyLogRow
from module.memory.daily_events import encode_event
from module.memory.project import from_row, to_record, to_view

TZ = ZoneInfo("Asia/Shanghai")


def row(
    role: str = "master",
    content: str = "你好",
    event_time: str = "2026-10-18T09:00:00.000+08:00",
    created_at: str = "2026-10-18T09:00:00.000+08:00",
    persona_id: str = "芙芙",
) -> DailyLogRow:
    return DailyLogRow(
        id=1,
        event_time=event_time,
        role=role,
        content=content,
        created_at=created_at,
        persona_id=persona_id,
    )


def test_from_row_semanticizes_event_time_role_and_created_at():
    entry = from_row(row())
    assert entry.event_time == datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    assert entry.role == "master"
    assert entry.created_at == datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    assert entry.created_at.tzinfo is not None


def test_from_row_rejects_unknown_role():
    with pytest.raises(ValueError, match="角色"):
        from_row(row(role="user"))


def test_to_record_formats_for_storage():
    record = to_record(from_row(row()))
    assert (record.event_time, record.role) == ("2026-10-18T09:00:00.000+08:00", "master")
    assert record.created_at == "2026-10-18T09:00:00.000+08:00"


def test_entry_to_record_to_entry_keeps_key_fields():
    entry = from_row(row(content="喜欢茶"))
    record = to_record(entry)
    again = from_row(
        DailyLogRow(
            id=2,
            event_time=record.event_time,
            role=record.role,
            content=record.content,
            created_at=record.created_at,
        )
    )
    assert (again.event_time, again.role, again.content) == (
        entry.event_time,
        entry.role,
        entry.content,
    )
    assert again.created_at == entry.created_at


def test_to_view_keeps_row_identity_and_renders_event_text():
    content = encode_event("session_boundary", persona="小桌")
    view = to_view(row(role="event", content=content))
    assert (view.id, view.event_time, view.role) == (
        1,
        "2026-10-18T09:00:00.000+08:00",
        "event",
    )
    assert view.content == "切换人设为小桌，新会话开始"


def test_to_view_keeps_plain_content_untouched():
    assert to_view(row(content="100% 确定")).content == "100% 确定"


def test_persona_id_survives_entry_and_record_projection():
    entry = from_row(row(persona_id="小桌"))
    record = to_record(entry)
    view = to_view(row(persona_id="小桌"))
    assert (entry.persona_id, record.persona_id, view.persona_id) == ("小桌", "小桌", "小桌")


def test_assistant_is_a_known_role():
    entry = from_row(row(role="assistant", content="查询结果"))
    assert entry.role == "assistant"
    assert to_view(row(role="assistant", content="查询结果")).role == "assistant"
