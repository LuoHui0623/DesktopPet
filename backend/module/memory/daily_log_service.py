from collections.abc import Callable, Sequence
from datetime import date, datetime, tzinfo
from typing import Protocol

from module.memory.contracts import DailyLogEntry, DailyLogRecord, DailyLogRow, DailyLogView
from module.memory.daily_events import encode_event
from module.memory.project import from_row, to_record, to_view

TEMPORARY_MODE_KEY = "memory.temporary_mode"


class DailyLogStore(Protocol):
    def append(self, records: Sequence[DailyLogRecord]) -> None: ...

    def by_date(self, date: str) -> list[DailyLogRow]: ...

    def dates(self) -> list[str]: ...

    def search(
        self,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        newest_first: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[DailyLogRow]: ...


class SettingStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def put(self, key: str, value: str) -> None: ...


def _system_now(timezone: tzinfo) -> datetime:
    return datetime.now(timezone)


class DailyLogService:
    def __init__(
        self,
        store: DailyLogStore,
        settings: SettingStore,
        timezone: tzinfo,
        clock: Callable[[tzinfo], datetime] = _system_now,
    ) -> None:
        self._store = store
        self._settings = settings
        self._timezone = timezone
        self._clock = clock

    def temporary_mode(self) -> bool:
        return self._settings.get(TEMPORARY_MODE_KEY) == "true"

    def set_temporary_mode(self, enabled: bool) -> None:
        self._settings.put(TEMPORARY_MODE_KEY, "true" if enabled else "false")

    def record_turn(
        self,
        user_text: str,
        assistant_text: str | Sequence[str],
        *,
        received_at: datetime | None = None,
        replied_at: datetime | None = None,
    ) -> bool:
        if self.temporary_mode():
            return False
        received = received_at or self._clock(self._timezone)
        replied = replied_at or self._clock(self._timezone)
        self._append(
            [
                DailyLogEntry(
                    day=received.date(), role="master", content=user_text, created_at=received
                ),
                *[
                    DailyLogEntry(day=replied.date(), role="me", content=text, created_at=replied)
                    for text in _utterances(assistant_text)
                ],
            ]
        )
        return True

    def record_reply(
        self, assistant_text: str | Sequence[str], *, at: datetime | None = None
    ) -> bool:
        if self.temporary_mode():
            return False
        now = at or self._clock(self._timezone)
        self._append(
            [
                DailyLogEntry(day=now.date(), role="me", content=text, created_at=now)
                for text in _utterances(assistant_text)
            ]
        )
        return True

    def record_event(self, event_type: str, **fields: str) -> bool:
        content = encode_event(event_type, **fields)
        if self.temporary_mode():
            return False
        now = self._clock(self._timezone)
        self._append([DailyLogEntry(day=now.date(), role="event", content=content, created_at=now)])
        return True

    def record_session_boundary(self, persona: str) -> bool:
        return self.record_event("session_boundary", persona=persona)

    def dates(self) -> list[str]:
        return self._store.dates()

    def read_day(self, day: str) -> list[DailyLogEntry]:
        return [from_row(row) for row in self._store.by_date(_iso_day(day))]

    def day_views(self, day: str) -> list[DailyLogView]:
        return [to_view(row) for row in self._store.by_date(_iso_day(day))]

    def search(
        self,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        newest_first: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[DailyLogView]:
        rows = self._store.search(
            keyword or None,
            _iso_day(date_from) if date_from else None,
            _iso_day(date_to) if date_to else None,
            newest_first=newest_first,
            limit=limit,
            offset=offset,
        )
        return [to_view(row) for row in rows]

    def _append(self, entries: Sequence[DailyLogEntry]) -> None:
        self._store.append([to_record(entry) for entry in entries])


def _utterances(assistant_text: str | Sequence[str]) -> tuple[str, ...]:
    """兼容旧的单字符串入参，统一归一化为有序的发言列表。"""
    if isinstance(assistant_text, str):
        return (assistant_text,)
    return tuple(assistant_text)


def _iso_day(day: str) -> str:
    return date.fromisoformat(day).isoformat()
