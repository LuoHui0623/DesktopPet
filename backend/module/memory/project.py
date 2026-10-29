from datetime import date, datetime
from typing import cast

from module.memory.contracts import (
    ROLES,
    DailyLogEntry,
    DailyLogRecord,
    DailyLogRole,
    DailyLogRow,
    DailyLogView,
)
from module.memory.daily_events import display_content


def _role(raw: str) -> DailyLogRole:
    if raw not in ROLES:
        raise ValueError(f"未知的 daily_log 角色：{raw!r}")
    return cast(DailyLogRole, raw)


def from_row(row: DailyLogRow) -> DailyLogEntry:
    return DailyLogEntry(
        day=date.fromisoformat(row.date),
        role=_role(row.role),
        content=row.content,
        created_at=datetime.fromisoformat(row.created_at),
    )


def to_record(entry: DailyLogEntry) -> DailyLogRecord:
    return DailyLogRecord(
        date=entry.day.isoformat(),
        role=entry.role,
        content=entry.content,
        created_at=entry.created_at.isoformat(timespec="milliseconds"),
    )


def to_view(row: DailyLogRow) -> DailyLogView:
    return DailyLogView(
        id=row.id,
        date=row.date,
        role=_role(row.role),
        content=display_content(row.role, row.content),
        created_at=row.created_at,
    )
