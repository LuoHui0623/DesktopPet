"""开口闸门：勿扰时段、最小间隔、每日限额与当日发送台账。"""

import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
from typing import Protocol

from agent.capabilities.perception.constants import (
    DEFAULT_DND_END,
    DEFAULT_DND_START,
    DEFAULT_MAX_PER_DAY,
    DEFAULT_MIN_INTERVAL_MINUTES,
    DEFAULT_SCHEDULER_ENABLED,
    LATE_NIGHT_END_HOUR,
    LATE_NIGHT_START_HOUR,
    MAX_DAILY_LIMIT,
    MAX_MIN_INTERVAL_MINUTES,
    SCHEDULER_SENT_KEY,
)
from agent.spec.settings import SectionValues, SettingsSource

logger = logging.getLogger("pet.scheduler")

SECTION = "scheduler"
HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class KeyValueStore(Protocol):
    """运行时台账存储：只有 `scheduler.sent` 这类非配置数据走这里。"""

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


def decode(values: SectionValues) -> SchedulerConfig:
    """段字段 → 调度配置；缺字段与非法取值回落默认值。"""
    return SchedulerConfig(
        enabled=_bool(values.get("enabled"), DEFAULT_SCHEDULER_ENABLED),
        min_interval_minutes=_int(
            values.get("min_interval_minutes"),
            DEFAULT_MIN_INTERVAL_MINUTES,
            MAX_MIN_INTERVAL_MINUTES,
        ),
        max_per_day=_int(values.get("max_per_day"), DEFAULT_MAX_PER_DAY, MAX_DAILY_LIMIT),
        dnd_start=_hhmm(values.get("dnd_start"), DEFAULT_DND_START),
        dnd_end=_hhmm(values.get("dnd_end"), DEFAULT_DND_END),
    )


def encode(config: SchedulerConfig) -> dict[str, object]:
    return {
        "enabled": config.enabled,
        "min_interval_minutes": config.min_interval_minutes,
        "max_per_day": config.max_per_day,
        "dnd_start": config.dnd_start,
        "dnd_end": config.dnd_end,
    }


class SchedulerSettings:
    """调度配置的领域状态：首次读时取 `scheduler` 段，写入只改内存。"""

    def __init__(self, source: SettingsSource) -> None:
        self._source = source
        self._state: SchedulerConfig | None = None

    def read(self) -> SchedulerConfig:
        if self._state is None:
            self._state = decode(self._source.section(SECTION))
        return self._state

    def write(self, config: SchedulerConfig) -> None:
        validate_config(config)
        self._state = config

    def snapshot(self) -> dict[str, object]:
        return encode(self.read())


class SchedulerLedgerService:
    def __init__(self, store: KeyValueStore, timezone: tzinfo) -> None:
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
        """只改领域状态；落盘由设置协调器负责。"""
        self._settings.write(config)
        return self._settings.read()

    def snapshot_settings(self) -> dict[str, object]:
        return self._settings.snapshot()

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


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off"}
    return default


def _int(value: object, default: int, high: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if 0 <= value <= high else default
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return default
        return parsed if 0 <= parsed <= high else default
    return default


def _hhmm(value: object, default: str) -> str:
    if not isinstance(value, str):
        return default
    text = value.strip()
    if HHMM.fullmatch(text) is None:
        return default
    return text


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
