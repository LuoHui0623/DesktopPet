from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from service.perception.events import PerceptionEventKind
from service.perception.models import PerceptionRuntimeState
from service.perception.parser import PerceptionEventParser
from service.perception.scheduler import (
    SchedulerLedgerService,
    SchedulerRuntime,
    SchedulerSettings,
)
from service.perception.settings import PerceptionConfig

TZ = ZoneInfo("Asia/Shanghai")
BASE = datetime(2026, 10, 29, 8, 0, tzinfo=TZ)


class MemoryStore(dict[str, str]):
    def put(self, key: str, value: str) -> None:
        self[key] = value


def config(**overrides) -> PerceptionConfig:
    values = {
        "enabled": True,
        "foreground_enabled": True,
        "idle_away_minutes": 10,
        "sit_minutes": 90,
        "whitelist": (),
        "muted": (),
    }
    values.update(overrides)
    return PerceptionConfig(**values)


def state(
    presence: str = "active",
    busy: bool = False,
    idle_ms: int = 0,
    foreground: str | None = None,
    processes: tuple[str, ...] = (),
) -> PerceptionRuntimeState:
    return PerceptionRuntimeState(
        presence=presence,
        busy=busy,
        idle_ms=idle_ms,
        foreground=foreground,
        processes=processes,
    )


def parser(store: MemoryStore | None = None) -> PerceptionEventParser:
    store = store or MemoryStore()
    runtime = SchedulerRuntime(
        SchedulerSettings(store),
        SchedulerLedgerService(store, TZ),
        timezone=TZ,
        clock=lambda zone: BASE,
    )
    return PerceptionEventParser(TZ, scheduler=runtime)


def after(**kwargs) -> datetime:
    return BASE + timedelta(**kwargs)


def kinds(instance: PerceptionEventParser, kind: PerceptionEventKind) -> list:
    return [event for event in instance.current_events() if event.kind is kind]


def test_presence_away_at_idle_threshold_and_return_triggers():
    instance = parser()
    instance.observe(state(idle_ms=0), BASE)
    instance.observe(state(idle_ms=599_999), after(seconds=1))
    instance.observe(state(presence="away", idle_ms=600_000), after(seconds=5))

    away = kinds(instance, PerceptionEventKind.PRESENCE_AWAY)
    assert len(away) == 1
    assert away[0].minutes == 10
    assert instance.tick(after(seconds=5), config()) == ()

    instance.observe(state(idle_ms=0), after(minutes=10))
    returned = kinds(instance, PerceptionEventKind.PRESENCE_RETURN)
    triggers = instance.tick(after(minutes=10), config())

    assert [trigger.topic for trigger in triggers] == ["greeting_return"]
    assert returned[0].minutes >= 10


def test_greeting_requires_ten_minutes_away():
    instance = parser()
    instance.observe(state(idle_ms=0), BASE)
    instance.observe(state(presence="away", idle_ms=300_000), after(seconds=5))
    instance.observe(state(idle_ms=0), after(minutes=1))

    assert instance.tick(after(minutes=1), config(idle_away_minutes=5)) == ()

    instance.observe(state(presence="away", idle_ms=300_000), after(minutes=2))
    instance.observe(state(idle_ms=0), after(minutes=7))
    triggers = instance.tick(after(minutes=7), config(idle_away_minutes=5))

    assert [trigger.topic for trigger in triggers] == ["greeting_return"]


def test_busy_enter_waits_three_seconds_and_exit_waits_thirty():
    instance = parser()
    instance.observe(state(busy=True), BASE)
    assert instance.tick(after(seconds=2), config()) == ()
    assert kinds(instance, PerceptionEventKind.BUSY_ENTER) == []

    assert instance.tick(after(seconds=3), config()) == ()
    assert len(kinds(instance, PerceptionEventKind.BUSY_ENTER)) == 1

    instance.observe(state(busy=False), after(minutes=40))
    assert instance.tick(after(minutes=40, seconds=29), config()) == ()
    assert kinds(instance, PerceptionEventKind.BUSY_EXIT) == []

    triggers = instance.tick(after(minutes=40, seconds=30), config())
    assert [trigger.topic for trigger in triggers] == ["busy_released"]
    assert "40" in triggers[0].reason


def test_busy_flicker_under_three_seconds_is_ignored():
    instance = parser()
    instance.observe(state(busy=True), BASE)
    instance.observe(state(busy=False), after(seconds=2))

    assert instance.tick(after(seconds=40), config()) == ()
    assert kinds(instance, PerceptionEventKind.BUSY_ENTER) == []
    assert kinds(instance, PerceptionEventKind.BUSY_EXIT) == []


def test_busy_release_is_cancelled_when_busy_returns_within_thirty_seconds():
    instance = parser()
    instance.observe(state(busy=True), BASE)
    instance.tick(after(seconds=3), config())
    instance.observe(state(busy=False), after(minutes=10))
    instance.observe(state(busy=True), after(minutes=10, seconds=20))
    assert instance.tick(after(minutes=10, seconds=25), config()) == ()

    instance.observe(state(busy=False), after(minutes=20))
    triggers = instance.tick(after(minutes=20, seconds=30), config())

    assert triggers == ()
    assert len(kinds(instance, PerceptionEventKind.BUSY_ENTER)) == 1
    assert len(kinds(instance, PerceptionEventKind.BUSY_EXIT)) == 1


def test_sit_long_fires_at_ninety_minutes_then_every_sixty():
    instance = parser()
    instance.observe(state(), BASE)

    assert instance.tick(after(minutes=89), config()) == ()
    first = instance.tick(after(minutes=90), config())
    assert instance.tick(after(minutes=149), config()) == ()
    second = instance.tick(after(minutes=150), config())

    assert [trigger.topic for trigger in first] == ["sit_long"]
    assert [trigger.topic for trigger in second] == ["sit_long"]


def test_away_resets_sit_long_streak():
    instance = parser()
    instance.observe(state(), BASE)
    instance.observe(state(presence="away", idle_ms=600_000), after(minutes=90))
    instance.observe(state(), after(minutes=100))
    instance.tick(after(minutes=100), config())

    assert instance.tick(after(minutes=189), config()) == ()
    assert [trigger.topic for trigger in instance.tick(after(minutes=190), config())] == [
        "sit_long"
    ]


def test_app_in_use_needs_three_continuous_minutes_and_does_not_repeat():
    instance = parser()
    instance.observe(state(foreground="code"), BASE)

    assert instance.tick(after(minutes=2, seconds=59), config()) == ()
    assert [trigger.topic for trigger in instance.tick(after(minutes=3), config())] == [
        "app_in_use.code"
    ]
    assert instance.tick(after(minutes=4), config()) == ()

    instance.observe(state(foreground="chrome"), after(minutes=5))
    assert [trigger.topic for trigger in instance.tick(after(minutes=8), config())] == [
        "app_in_use.chrome"
    ]

    instance.observe(state(foreground="code"), after(minutes=9))
    assert [trigger.topic for trigger in instance.tick(after(minutes=12), config())] == [
        "app_in_use.code"
    ]


def test_recent_context_keeps_only_the_latest_app_in_use():
    store = MemoryStore()
    store["scheduler.enabled"] = "false"
    instance = parser(store)
    instance.observe(state(foreground="code"), BASE)
    instance.tick(after(minutes=3), config())
    instance.observe(state(foreground="chrome"), after(minutes=4))
    instance.tick(after(minutes=7), config())

    events = instance.take_context_events(3)

    assert [event.kind for event in events] == [PerceptionEventKind.APP_IN_USE]
    assert events[0].exe == "chrome"


def test_app_launch_needs_thirty_seconds_and_quit_reports_run_minutes():
    instance = parser()
    instance.observe(state(processes=("code",)), BASE)
    instance.observe(state(processes=("code", "chrome")), after(seconds=10))

    assert instance.tick(after(seconds=39), config()) == ()
    assert [trigger.topic for trigger in instance.tick(after(seconds=40), config())] == [
        "app_launched.chrome"
    ]
    assert instance.tick(after(seconds=50), config()) == ()

    instance.observe(state(processes=("code",)), after(minutes=50))
    triggers = instance.tick(after(minutes=50), config())

    assert [trigger.topic for trigger in triggers] == ["app_quit.chrome"]
    assert "49" in triggers[0].reason


def test_short_run_quit_is_recorded_without_trigger():
    instance = parser()
    instance.observe(state(processes=("code",)), BASE)
    instance.observe(state(processes=("code", "chrome")), after(seconds=10))
    instance.tick(after(seconds=40), config())
    instance.observe(state(processes=("code",)), after(minutes=5))

    assert instance.tick(after(minutes=5), config()) == ()
    assert kinds(instance, PerceptionEventKind.APP_QUIT)[0].minutes == 4


def test_trigger_flushes_current_events_to_the_rule_step():
    instance = parser()
    instance.observe(state(foreground="code"), BASE)

    triggers = instance.tick(after(minutes=3), config())

    assert [trigger.topic for trigger in triggers] == ["app_in_use.code"]
    assert instance.current_events() == ()


def test_untriggered_events_stay_current_until_context_reads_them():
    store = MemoryStore()
    store["scheduler.enabled"] = "false"
    instance = parser(store)
    instance.observe(state(foreground="code"), BASE)

    assert instance.tick(after(minutes=3), config()) == ()
    assert [event.kind for event in instance.take_context_events(3)] == [
        PerceptionEventKind.APP_IN_USE
    ]
    assert instance.take_context_events(3) == ()


def test_late_night_triggers_once_after_midnight():
    instance = parser()
    instance.observe(state(), BASE)

    late = datetime(2026, 10, 29, 23, 30, tzinfo=TZ)
    assert instance.tick(late, config(sit_minutes=10_000)) == ()
    assert len(kinds(instance, PerceptionEventKind.LATE_NIGHT)) == 1

    midnight = datetime(2026, 10, 30, 0, 0, tzinfo=TZ)
    triggers = instance.tick(midnight, config(sit_minutes=10_000))
    assert [trigger.topic for trigger in triggers] == ["late_night"]
    assert instance.tick(midnight + timedelta(minutes=5), config(sit_minutes=10_000)) == ()


def test_triggers_are_suppressed_while_away():
    instance = parser()
    instance.observe(state(processes=("code",)), BASE)
    instance.observe(state(processes=("code", "chrome")), after(seconds=10))
    instance.tick(after(seconds=40), config())
    instance.observe(
        state(presence="away", idle_ms=600_000, processes=("code", "chrome")),
        after(minutes=2),
    )
    instance.observe(
        state(presence="away", idle_ms=600_000, processes=("code",)),
        after(minutes=20),
    )

    triggers = instance.tick(after(minutes=20), config())

    assert triggers == ()
    assert kinds(instance, PerceptionEventKind.APP_QUIT)


def test_app_in_use_event_is_recorded_but_not_triggered_while_busy():
    instance = parser()
    instance.observe(state(foreground="code"), BASE)
    instance.observe(state(busy=True, foreground="code"), after(minutes=1))

    triggers = instance.tick(after(minutes=3), config())

    assert triggers == ()
    assert len(kinds(instance, PerceptionEventKind.APP_IN_USE)) == 1
