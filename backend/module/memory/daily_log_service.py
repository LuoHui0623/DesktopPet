from collections.abc import Callable, Sequence
from datetime import date, datetime, tzinfo
from sqlite3 import IntegrityError
from typing import Protocol

from constants.agent import DEFAULT_PERSONA_ID, PERSONA_ACTIVE_KEY
from module.memory.contracts import (
    CHAT_ROLES,
    RECOLLECTION_ROLE,
    DailyLogEntry,
    DailyLogRecord,
    DailyLogRole,
    DailyLogRow,
    DailyLogView,
)
from module.memory.daily_events import encode_event
from module.memory.project import from_row, to_record, to_view


class DailyLogStore(Protocol):
    def append(self, records: Sequence[DailyLogRecord]) -> None: ...

    def by_date(self, day: str, *, persona_id: str | None = None) -> list[DailyLogRow]: ...

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
        persona_id: str | None = None,
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

    def active_persona(self) -> str:
        return self._settings.get(PERSONA_ACTIVE_KEY) or DEFAULT_PERSONA_ID

    def record_turn(
        self,
        user_text: str,
        assistant_text: str | Sequence[str],
        *,
        received_at: datetime | None = None,
        replied_at: datetime | None = None,
        persona_id: str | None = None,
    ) -> bool:
        received = received_at or self._clock(self._timezone)
        replied = replied_at or self._clock(self._timezone)
        persona = self._persona(persona_id)
        self._append(
            [
                DailyLogEntry(
                    event_time=received,
                    role="master",
                    content=user_text,
                    created_at=received,
                    persona_id=persona,
                ),
                *[
                    DailyLogEntry(
                        event_time=replied,
                        role="me",
                        content=text,
                        created_at=replied,
                        persona_id=persona,
                    )
                    for text in _utterances(assistant_text)
                ],
            ]
        )
        return True

    def record_user(
        self, text: str, *, at: datetime | None = None, persona_id: str | None = None
    ) -> bool:
        now = at or self._clock(self._timezone)
        return self._record_single("master", text, now, persona_id)

    def record_reply(
        self,
        assistant_text: str | Sequence[str],
        *,
        at: datetime | None = None,
        persona_id: str | None = None,
    ) -> bool:
        now = at or self._clock(self._timezone)
        return self._record_many("me", assistant_text, now, persona_id)

    def record_assistant(
        self,
        assistant_text: str | Sequence[str],
        *,
        at: datetime | None = None,
        persona_id: str | None = None,
    ) -> bool:
        now = at or self._clock(self._timezone)
        return self._record_many("assistant", assistant_text, now, persona_id)

    def record_event(
        self, event_type: str, *, persona_id: str | None = None, **fields: str
    ) -> bool:
        content = encode_event(event_type, **fields)
        now = self._clock(self._timezone)
        self._append(
            [
                DailyLogEntry(
                    event_time=now,
                    role="event",
                    content=content,
                    created_at=now,
                    persona_id=self._persona(persona_id),
                )
            ]
        )
        return True

    def record_session_boundary(self, persona: str) -> bool:
        return self.record_event("session_boundary", persona=persona)

    def record_recollection(
        self, day: str, content: str, *, persona_id: str | None = None
    ) -> DailyLogEntry | None:
        """写下当天的回忆；同一人设同一天只保留第一条，重复调用返回既有记录。"""
        persona = self._persona(persona_id)
        iso = _iso_day(day)
        existing = self.read_recollection(iso, persona_id=persona)
        if existing is not None:
            return existing

        now = self._clock(self._timezone)
        entry = DailyLogEntry(
            event_time=_moment_of(iso, now),
            role=RECOLLECTION_ROLE,
            content=content,
            created_at=now,
            persona_id=persona,
        )
        try:
            self._append([entry])
        except IntegrityError:
            # 并发下唯一索引兜底：读回先落库的那一条。
            winner = self.read_recollection(iso, persona_id=persona)
            if winner is None:
                raise
            return winner
        return entry

    def read_recollection(self, day: str, *, persona_id: str | None = None) -> DailyLogEntry | None:
        rows = self._store.by_date(_iso_day(day), persona_id=self._persona(persona_id))
        for row in rows:
            if row.role == RECOLLECTION_ROLE:
                return from_row(row)
        return None

    def has_recollection(self, day: str, *, persona_id: str | None = None) -> bool:
        return self.read_recollection(day, persona_id=persona_id) is not None

    def dates(self) -> list[str]:
        return self._store.dates()

    def read_day(self, day: str, *, persona_id: str | None = None) -> list[DailyLogEntry]:
        rows = self._store.by_date(_iso_day(day), persona_id=self._persona(persona_id))
        return [from_row(row) for row in rows]

    def restore_day(self, day: str, *, persona_id: str | None = None) -> list[DailyLogEntry]:
        """当天可恢复进上下文的聊天记录；事件与系统消息不恢复。"""
        rows = self._store.by_date(_iso_day(day), persona_id=self._persona(persona_id))
        return [from_row(row) for row in rows if row.role in CHAT_ROLES]

    def day_views(self, day: str, *, persona_id: str | None = None) -> list[DailyLogView]:
        rows = self._store.by_date(_iso_day(day), persona_id=self._persona(persona_id))
        return [to_view(row) for row in rows]

    def search(
        self,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        newest_first: bool = False,
        limit: int | None = None,
        offset: int = 0,
        persona_id: str | None = None,
    ) -> list[DailyLogView]:
        rows = self._store.search(
            keyword or None,
            _iso_day(date_from) if date_from else None,
            _iso_day(date_to) if date_to else None,
            newest_first=newest_first,
            limit=limit,
            offset=offset,
            persona_id=persona_id,
        )
        return [to_view(row) for row in rows]

    def _record_single(
        self, role: DailyLogRole, text: str, at: datetime, persona_id: str | None
    ) -> bool:
        return self._record_many(role, (text,), at, persona_id)

    def _record_many(
        self,
        role: DailyLogRole,
        texts: str | Sequence[str],
        at: datetime,
        persona_id: str | None,
    ) -> bool:
        persona = self._persona(persona_id)
        self._append(
            [
                DailyLogEntry(
                    event_time=at,
                    role=role,
                    content=text,
                    created_at=at,
                    persona_id=persona,
                )
                for text in _utterances(texts)
            ]
        )
        return True

    def _persona(self, persona_id: str | None) -> str:
        return persona_id or self.active_persona()

    def _append(self, entries: Sequence[DailyLogEntry]) -> None:
        self._store.append([to_record(entry) for entry in entries])


def _utterances(assistant_text: str | Sequence[str]) -> tuple[str, ...]:
    """兼容旧的单字符串入参，统一归一化为有序的发言列表。"""
    if isinstance(assistant_text, str):
        return (assistant_text,)
    return tuple(assistant_text)


def _iso_day(day: str) -> str:
    return date.fromisoformat(day).isoformat()


def _moment_of(day: str, now: datetime) -> datetime:
    """回忆挂在所属日期上，时刻沿用生成时刻，便于按日排序又保留生成信息。"""
    return datetime.combine(date.fromisoformat(day), now.timetz())
