import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from service.perception.constants import (
    SCHEDULER_DND_START_KEY,
    SCHEDULER_MAX_PER_DAY_KEY,
    SCHEDULER_SENT_KEY,
)
from service.perception.models import PerceptionRuntimeState
from service.perception.parser import PerceptionEventParser
from service.perception.scheduler import (
    InvalidSchedulerConfig,
    SchedulerConfig,
    SchedulerLedger,
    SchedulerLedgerService,
    SchedulerRuntime,
    SchedulerSettings,
    in_quiet_hours,
    is_deep_night,
)
from service.perception.settings import PerceptionConfig

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)


class MemoryStore(dict[str, str]):
    def put(self, key: str, value: str) -> None:
        self[key] = value


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


def make(
    store: MemoryStore | None = None,
    *,
    at: datetime = NOW,
    chat_active=None,
) -> tuple[MemoryStore, SchedulerRuntime]:
    store = store if store is not None else MemoryStore()
    runtime = SchedulerRuntime(
        SchedulerSettings(store),
        SchedulerLedgerService(store, TZ),
        timezone=TZ,
        clock=Clock(at),
        chat_active=chat_active,
    )
    return store, runtime


def config(**overrides) -> SchedulerConfig:
    values = {
        "enabled": True,
        "min_interval_minutes": 0,
        "max_per_day": 20,
        "dnd_start": "00:00",
        "dnd_end": "00:00",
    }
    values.update(overrides)
    return SchedulerConfig(**values)


def parser(runtime: SchedulerRuntime, at: datetime = NOW) -> PerceptionEventParser:
    instance = PerceptionEventParser(TZ, scheduler=runtime)
    instance.observe(PerceptionRuntimeState("active", False, 0, None, ()), at)
    return instance


def test_config_defaults_match_the_agreed_values():
    _, runtime = make()

    stored = runtime.config()

    assert stored == SchedulerConfig(True, 60, 20, "22:30", "08:00")


def test_config_round_trip_and_invalid_values_are_rejected():
    store, runtime = make()

    saved = runtime.write_config(config(enabled=False, min_interval_minutes=30, max_per_day=8))

    assert saved == SchedulerConfig(False, 30, 8, "00:00", "00:00")
    assert SchedulerSettings(store).read() == saved
    with pytest.raises(InvalidSchedulerConfig, match="时间格式"):
        runtime.write_config(config(dnd_start="24:00"))
    with pytest.raises(InvalidSchedulerConfig, match="每日上限"):
        runtime.write_config(config(max_per_day=101))


def test_broken_stored_config_falls_back_to_defaults():
    store = MemoryStore()
    store[SCHEDULER_MAX_PER_DAY_KEY] = "many"
    store[SCHEDULER_DND_START_KEY] = "25:00"

    stored = SchedulerSettings(store).read()

    assert (stored.max_per_day, stored.dnd_start) == (20, "22:30")


def test_quiet_hours_cross_midnight():
    window = config(dnd_start="22:30", dnd_end="08:00")

    assert not in_quiet_hours(NOW.replace(hour=22, minute=29), window)
    assert in_quiet_hours(NOW.replace(hour=22, minute=30), window)
    assert in_quiet_hours(NOW.replace(hour=23, minute=59), window)
    assert in_quiet_hours(NOW.replace(hour=0, minute=0), window)
    assert in_quiet_hours(NOW.replace(hour=7, minute=59), window)
    assert not in_quiet_hours(NOW.replace(hour=8, minute=0), window)


def test_quiet_hours_within_one_day():
    window = config(dnd_start="12:00", dnd_end="13:00")

    assert not in_quiet_hours(NOW.replace(hour=11, minute=59), window)
    assert in_quiet_hours(NOW.replace(hour=12, minute=0), window)
    assert in_quiet_hours(NOW.replace(hour=12, minute=59), window)
    assert not in_quiet_hours(NOW.replace(hour=13, minute=0), window)


def test_equal_bounds_mean_no_quiet_hours():
    assert not in_quiet_hours(NOW, config(dnd_start="00:00", dnd_end="00:00"))


def test_deep_night_window():
    assert not is_deep_night(NOW.replace(hour=22, minute=59))
    assert is_deep_night(NOW.replace(hour=23, minute=0))
    assert is_deep_night(NOW.replace(hour=4, minute=59))
    assert not is_deep_night(NOW.replace(hour=5, minute=0))


def test_empty_ledger_starts_at_today():
    _, runtime = make()

    ledger = runtime.ledger(NOW)

    assert isinstance(ledger, SchedulerLedger)
    assert (ledger.day.isoformat(), dict(ledger.topics), ledger.total) == (
        "2026-10-29",
        {},
        0,
    )


def test_record_increments_topic_and_total():
    _, runtime = make()

    runtime.record_sent("greeting_return")
    ledger = runtime.record_sent("greeting_return")
    assert dict(ledger.topics) == {"greeting_return": 2}
    assert ledger.total == 2

    ledger = runtime.record_sent("sit_long")
    assert dict(ledger.topics) == {"greeting_return": 2, "sit_long": 1}
    assert ledger.total == 3
    assert runtime.last_sent_at == NOW


def test_ledger_survives_a_restart_on_the_same_day():
    store, runtime = make()
    runtime.record_sent("greeting_return")

    _, restarted = make(store)

    assert dict(restarted.ledger(NOW).topics) == {"greeting_return": 1}


def test_ledger_resets_on_a_new_day():
    store, runtime = make()
    runtime.record_sent("greeting_return")

    next_day = NOW + timedelta(days=1)
    assert make(store, at=next_day)[1].ledger(next_day).total == 0


def test_stored_ledger_uses_the_agreed_shape():
    store, runtime = make()
    runtime.record_sent("greeting_return")

    assert json.loads(store[SCHEDULER_SENT_KEY]) == {
        "date": "2026-10-29",
        "topics": {"greeting_return": 1},
        "total": 1,
    }


def test_corrupt_ledger_is_treated_as_empty():
    store = MemoryStore()
    store[SCHEDULER_SENT_KEY] = "{not json"
    _, runtime = make(store)

    assert runtime.ledger(NOW).total == 0
    assert runtime.record_sent("greeting_return").total == 1


def test_blank_topic_is_rejected():
    _, runtime = make()

    with pytest.raises(ValueError, match="主题键"):
        runtime.record_sent("   ")


def test_denial_reason_reports_presence_and_busy():
    _, runtime = make()
    instance = parser(runtime)
    instance.observe(PerceptionRuntimeState("away", False, 600_000, None, ()), NOW)
    assert instance.denial_reason("greeting_return", NOW) == "away"

    instance.observe(PerceptionRuntimeState("active", True, 0, None, ()), NOW)
    assert instance.denial_reason("app_in_use.code", NOW) == "busy"


def test_denial_reason_reports_quiet_hours_across_midnight():
    store, runtime = make()
    runtime.write_config(config(dnd_start="22:30", dnd_end="08:00"))
    instance = parser(runtime, NOW.replace(hour=23, minute=0))

    assert instance.denial_reason("sit_long", NOW.replace(hour=23, minute=0)) == "quiet_hours"
    assert instance.denial_reason("sit_long", NOW.replace(hour=8, minute=0)) is None


def test_late_night_bypasses_quiet_hours_only_in_deep_night():
    store, runtime = make()
    runtime.write_config(config(dnd_start="22:30", dnd_end="08:00"))
    instance = parser(runtime, NOW.replace(hour=1, minute=0))

    assert instance.denial_reason("late_night", NOW.replace(hour=1, minute=0)) is None
    assert instance.denial_reason("sit_long", NOW.replace(hour=1, minute=0)) == "quiet_hours"
    assert instance.denial_reason("late_night", NOW.replace(hour=22, minute=45)) == "quiet_hours"


def test_denial_reason_reports_scheduler_disabled():
    _, runtime = make()
    runtime.write_config(config(enabled=False))

    assert parser(runtime).denial_reason("sit_long", NOW) == "scheduler_disabled"


def test_denial_reason_reports_active_chat():
    _, runtime = make(chat_active=lambda: True)

    assert parser(runtime).denial_reason("sit_long", NOW) == "chatting"


def test_denial_reason_enforces_minimum_interval():
    _, runtime = make()
    instance = parser(runtime)
    runtime.write_config(config(min_interval_minutes=60))
    runtime.record_sent("app_launched.code", NOW)

    assert instance.denial_reason("app_in_use.code", NOW + timedelta(minutes=59)) == "min_interval"
    assert instance.denial_reason("app_in_use.code", NOW + timedelta(minutes=60)) is None


def test_denial_reason_enforces_daily_limit():
    _, runtime = make()
    instance = parser(runtime)
    runtime.write_config(config(max_per_day=2))
    runtime.record_sent("greeting_return", NOW)
    runtime.record_sent("sit_long", NOW)

    assert instance.denial_reason("app_in_use.code", NOW) == "daily_limit"


def test_denial_reason_dedupes_topics_per_day():
    _, runtime = make()
    runtime.write_config(config())
    instance = parser(runtime)
    runtime.record_sent("app_in_use.code", NOW)

    assert instance.denial_reason("app_in_use.code", NOW) == "topic_limit"


def test_greeting_and_sit_allow_three_per_day():
    _, runtime = make()
    runtime.write_config(config())
    instance = parser(runtime)

    for _ in range(3):
        runtime.record_sent("greeting_return", NOW)
    assert instance.denial_reason("greeting_return", NOW) == "topic_limit"

    for _ in range(3):
        runtime.record_sent("sit_long", NOW)
    assert instance.denial_reason("sit_long", NOW) == "topic_limit"


def test_deep_night_only_allows_late_night_topic():
    _, runtime = make()
    runtime.write_config(config())
    instance = parser(runtime, NOW.replace(hour=1, minute=0))
    one_am = NOW.replace(hour=1, minute=0)

    assert instance.denial_reason("sit_long", one_am) == "deep_night"
    assert instance.denial_reason("late_night", one_am) is None


def test_scheduler_config_reads_only_app_setting():
    """调度配置只读 app_setting：不再有 .env 迁移路径，未写入时按常量默认。"""
    store = MemoryStore()
    settings = SchedulerSettings(store)

    assert settings.read() == SchedulerConfig(
        enabled=True,
        min_interval_minutes=60,
        max_per_day=20,
        dnd_start="22:30",
        dnd_end="08:00",
    )

    store[SCHEDULER_DND_START_KEY] = "23:00"
    store[SCHEDULER_MAX_PER_DAY_KEY] = "15"

    saved = settings.read()
    assert saved.dnd_start == "23:00"
    assert saved.max_per_day == 15


def test_parser_logs_denial_reason_once_per_change(caplog):
    _, runtime = make()
    runtime.write_config(config(min_interval_minutes=60))
    at = datetime(2026, 10, 30, 1, 0, tzinfo=TZ)
    runtime.record_sent("app_launched.code", at - timedelta(minutes=10))
    instance = parser(runtime, at)

    with caplog.at_level(logging.INFO, logger="pet.perception"):
        assert instance.tick(at, _perception_config()) == ()
        assert instance.tick(at + timedelta(minutes=5), _perception_config()) == ()

    messages = [record.message for record in caplog.records if "主动判定" in record.message]
    assert len(messages) == 1
    assert "min_interval" in messages[0]


def _perception_config() -> PerceptionConfig:
    return PerceptionConfig(
        enabled=True,
        foreground_enabled=True,
        idle_away_minutes=10,
        sit_minutes=90,
        whitelist=(),
        muted=(),
    )
