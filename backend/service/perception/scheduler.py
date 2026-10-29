import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
from typing import Protocol

from service.perception.constants import (
    DEFAULT_DND_END,
    DEFAULT_DND_START,
    DEFAULT_MAX_PER_DAY,
    DEFAULT_MIN_INTERVAL_MINUTES,
    DEFAULT_SCHEDULER_ENABLED,
    LATE_NIGHT_END_HOUR,
    LATE_NIGHT_START_HOUR,
    MAX_DAILY_LIMIT,
    MAX_MIN_INTERVAL_MINUTES,
    SCHEDULER_DND_END_KEY,
    SCHEDULER_DND_START_KEY,
    SCHEDULER_ENABLED_KEY,
    SCHEDULER_MAX_PER_DAY_KEY,
    SCHEDULER_MIN_INTERVAL_KEY,
    SCHEDULER_SENT_KEY,
)

logger = logging.getLogger("pet.scheduler")

HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class SettingStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def put(self, key: str, value: str) -> None: ...


class InvalidSchedulerConfig(ValueError):
    pass


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool
    min_interval_minutes: int
    max_per_day: int
    dnd_start: str
    dnd_end: str


@dataclass(frozen=True)
class SchedulerLedger:
    day: date
    topics: Mapping[str, int]
    total: int


class SchedulerSettings:
    def __init__(self, store: SettingStore) -> None:
        self._store = store

    def read(self) -> SchedulerConfig:
        return SchedulerConfig(
            enabled=_bool(self._store.get(SCHEDULER_ENABLED_KEY), DEFAULT_SCHEDULER_ENABLED),
            min_interval_minutes=_int(
                self._store.get(SCHEDULER_MIN_INTERVAL_KEY),
                DEFAULT_MIN_INTERVAL_MINUTES,
                MAX_MIN_INTERVAL_MINUTES,
            ),
            max_per_day=_int(
                self._store.get(SCHEDULER_MAX_PER_DAY_KEY),
                DEFAULT_MAX_PER_DAY,
                MAX_DAILY_LIMIT,
            ),
            dnd_start=_hhmm(self._store.get(SCHEDULER_DND_START_KEY), DEFAULT_DND_START),
            dnd_end=_hhmm(self._store.get(SCHEDULER_DND_END_KEY), DEFAULT_DND_END),
        )

    def write(self, config: SchedulerConfig) -> None:
        validate_config(config)
        self._store.put(SCHEDULER_ENABLED_KEY, "true" if config.enabled else "false")
        self._store.put(SCHEDULER_MIN_INTERVAL_KEY, str(config.min_interval_minutes))
        self._store.put(SCHEDULER_MAX_PER_DAY_KEY, str(config.max_per_day))
        self._store.put(SCHEDULER_DND_START_KEY, config.dnd_start)
        self._store.put(SCHEDULER_DND_END_KEY, config.dnd_end)


class SchedulerLedgerService:
    def __init__(self, store: SettingStore, timezone: tzinfo) -> None:
        self._store = store
        self._timezone = timezone

    def read(self, at: datetime) -> SchedulerLedger:
        today = at.astimezone(self._timezone).date()
        return _decode_ledger(self._store.get(SCHEDULER_SENT_KEY), today)

    def record(self, topic: str, at: datetime) -> SchedulerLedger:
        key = topic.strip()
        if not key:
            raise ValueError("主题键不能为空")
        ledger = self.read(at)
        topics = dict(ledger.topics)
        topics[key] = topics.get(key, 0) + 1
        updated = SchedulerLedger(day=ledger.day, topics=topics, total=sum(topics.values()))
        self._store.put(SCHEDULER_SENT_KEY, _encode_ledger(updated))
        return updated


class SchedulerRuntime:
    def __init__(
        self,
        settings: SchedulerSettings,
        ledger: SchedulerLedgerService,
        *,
        timezone: tzinfo,
        clock: Callable[[tzinfo], datetime] | None = None,
        chat_active: Callable[[], bool] | None = None,
    ) -> None:
        self._settings = settings
        self._ledger = ledger
        self._timezone = timezone
        self._clock = clock or (lambda zone: datetime.now(zone))
        self._chat_active = chat_active
        # 台账只存当日计数，重启后无法得知上次发送时刻，最小间隔从空开始
        self._last_sent_at: datetime | None = None

    def now(self) -> datetime:
        return self._clock(self._timezone)

    def config(self) -> SchedulerConfig:
        return self._settings.read()

    def write_config(self, config: SchedulerConfig) -> SchedulerConfig:
        self._settings.write(config)
        return self._settings.read()

    def ledger(self, at: datetime | None = None) -> SchedulerLedger:
        return self._ledger.read(at or self.now())

    def record_sent(self, topic: str, at: datetime | None = None) -> SchedulerLedger:
        moment = at or self.now()
        ledger = self._ledger.record(topic, moment)
        self._last_sent_at = moment
        return ledger

    @property
    def last_sent_at(self) -> datetime | None:
        return self._last_sent_at

    @property
    def chatting(self) -> bool:
        return self._chat_active is not None and self._chat_active()


def validate_config(config: SchedulerConfig) -> None:
    if not 0 <= config.min_interval_minutes <= MAX_MIN_INTERVAL_MINUTES:
        raise InvalidSchedulerConfig(
            f"最小间隔必须在 0 到 {MAX_MIN_INTERVAL_MINUTES} 分钟之间，当前为 "
            f"{config.min_interval_minutes}"
        )
    if not 0 <= config.max_per_day <= MAX_DAILY_LIMIT:
        raise InvalidSchedulerConfig(
            f"每日上限必须在 0 到 {MAX_DAILY_LIMIT} 次之间，当前为 {config.max_per_day}"
        )
    _parse_hhmm(config.dnd_start)
    _parse_hhmm(config.dnd_end)


def in_quiet_hours(local: datetime, config: SchedulerConfig) -> bool:
    start = _parse_hhmm(config.dnd_start)
    end = _parse_hhmm(config.dnd_end)
    if start == end:
        return False
    current = local.time().replace(second=0, microsecond=0)
    if start < end:
        return start <= current < end
    return current >= start or current < end


def is_deep_night(local: datetime) -> bool:
    return local.hour >= LATE_NIGHT_START_HOUR or local.hour < LATE_NIGHT_END_HOUR


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def _int(raw: str | None, default: int, high: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if 0 <= value <= high else default


def _hhmm(raw: str | None, default: str) -> str:
    if raw is None or HHMM.fullmatch(raw.strip()) is None:
        return default
    return raw.strip()


def _parse_hhmm(raw: str) -> time:
    match = HHMM.fullmatch(raw.strip())
    if match is None:
        raise InvalidSchedulerConfig(f"时间格式应为 HH:MM，当前为 {raw!r}")
    return time(int(match.group(1)), int(match.group(2)))


def _encode_ledger(ledger: SchedulerLedger) -> str:
    body = {
        "date": ledger.day.isoformat(),
        "topics": dict(ledger.topics),
        "total": ledger.total,
    }
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


def _decode_ledger(raw: str | None, today: date) -> SchedulerLedger:
    empty = SchedulerLedger(day=today, topics={}, total=0)
    if not raw:
        return empty
    try:
        data = json.loads(raw)
    except ValueError:
        return empty
    if not isinstance(data, dict) or data.get("date") != today.isoformat():
        return empty
    raw_topics = data.get("topics")
    if not isinstance(raw_topics, dict):
        return empty
    topics: dict[str, int] = {}
    for key, count in raw_topics.items():
        if not isinstance(key, str) or not isinstance(count, int) or isinstance(count, bool):
            return empty
        if count < 0:
            return empty
        topics[key] = count
    return SchedulerLedger(day=today, topics=topics, total=sum(topics.values()))
