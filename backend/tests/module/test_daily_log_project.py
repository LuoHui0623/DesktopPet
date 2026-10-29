from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from module.memory.contracts import DailyLogRow
from module.memory.daily_events import encode_event
from module.memory.project import from_row, to_record, to_view

TZ = ZoneInfo("Asia/Shanghai")


def row(
    role: str = "master",
    content: str = "你好",
    day: str = "2026-10-18",
    created_at: str = "2026-10-18T09:00:00.000+08:00",
) -> DailyLogRow:
    return DailyLogRow(id=1, date=day, role=role, content=content, created_at=created_at)


def test_from_row_semanticizes_day_role_and_time():
    entry = from_row(row())
    assert entry.day == date(2026, 10, 18)
    assert entry.role == "master"
    assert entry.created_at == datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    assert entry.created_at.tzinfo is not None


def test_from_row_rejects_unknown_role():
    with pytest.raises(ValueError, match="角色"):
        from_row(row(role="user"))


def test_to_record_formats_for_storage():
    record = to_record(from_row(row()))
    assert (record.date, record.role) == ("2026-10-18", "master")
    assert record.created_at == "2026-10-18T09:00:00.000+08:00"


def test_entry_to_record_to_entry_keeps_key_fields():
    entry = from_row(row(content="喜欢茶"))
    record = to_record(entry)
    again = from_row(
        DailyLogRow(
            id=2,
            date=record.date,
            role=record.role,
            content=record.content,
            created_at=record.created_at,
        )
    )
    assert (again.day, again.role, again.content) == (entry.day, entry.role, entry.content)
    assert again.created_at == entry.created_at


def test_to_view_keeps_row_identity_and_renders_event_text():
    content = encode_event("session_boundary", persona="小桌")
    view = to_view(row(role="event", content=content))
    assert (view.id, view.date, view.role) == (1, "2026-10-18", "event")
    assert view.content == "切换人设为小桌，新会话开始"


def test_to_view_keeps_plain_content_untouched():
    assert to_view(row(content="100% 确定")).content == "100% 确定"
