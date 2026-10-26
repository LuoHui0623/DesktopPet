from collections.abc import Callable
from datetime import date, datetime, tzinfo
from typing import Protocol

from infra.store.repositories.daily_log import DailyLogRecord

TEMPORARY_MODE_KEY = "memory.temporary_mode"


class DailyLogStore(Protocol):
    def append(self, date: str, created_at: str, entries: list[tuple[str, str]]) -> None: ...

    def by_date(self, date: str) -> list[DailyLogRecord]: ...

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
    ) -> list[DailyLogRecord]: ...


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

    def record_turn(self, user_text: str, assistant_text: str) -> bool:
        if self.temporary_mode():
            return False
        now = self._clock(self._timezone)
        self._store.append(
            now.date().isoformat(),
            now.isoformat(timespec="milliseconds"),
            [("user", user_text), ("assistant", assistant_text)],
        )
        return True

    def dates(self) -> list[str]:
        return self._store.dates()

    def read_day(self, day: str) -> list[DailyLogRecord]:
        return self._store.by_date(date.fromisoformat(day).isoformat())

    def search(
        self,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        newest_first: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[DailyLogRecord]:
        start = date.fromisoformat(date_from).isoformat() if date_from else None
        end = date.fromisoformat(date_to).isoformat() if date_to else None
        return self._store.search(
            keyword or None,
            start,
            end,
            newest_first=newest_first,
            limit=limit,
            offset=offset,
        )
