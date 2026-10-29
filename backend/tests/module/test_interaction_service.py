from datetime import UTC, datetime, timedelta

from module.interaction import constants
from module.interaction.config import DEFAULT_INTERACTION_CONFIG, INTERACTION_CONFIG_KEY
from module.interaction.contracts import InteractionOutcome
from module.interaction.interaction_service import InteractionService


class MemorySettings:
    """互动配置的真源是 app_setting，测试用内存字典代替数据库。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_service(clock: FakeClock) -> InteractionService:
    return InteractionService(clock=clock)


def test_records_and_drains_interactions_once():
    clock = FakeClock(datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    service = make_service(clock)

    assert service.record("click").accepted is True
    clock.advance(1)
    assert service.record("drag").accepted is True

    drained = service.drain()
    assert [item.kind for item in drained] == ["click", "drag"]
    assert drained[0].label == "单击宠物"
    assert service.drain() == ()


def test_same_interaction_is_throttled_within_the_window():
    clock = FakeClock(datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    service = make_service(clock)

    for _ in range(10):
        service.record("click")
        clock.advance(0.1)

    assert [item.kind for item in service.drain()] == ["click"]


def test_different_kinds_do_not_share_the_throttle():
    clock = FakeClock(datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    service = make_service(clock)

    assert service.record("click").accepted is True
    clock.advance(0.1)
    assert service.record("hover").accepted is True
    clock.advance(0.1)
    assert service.record("click").accepted is False


def test_throttle_window_expires():
    clock = FakeClock(datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    service = make_service(clock)

    service.record("click")
    clock.advance(constants.THROTTLE_SECONDS)
    assert service.record("click").accepted is True


def test_pending_queue_is_capped():
    clock = FakeClock(datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    service = make_service(clock)

    for index in range(constants.PENDING_LIMIT + 3):
        clock.advance(constants.THROTTLE_SECONDS)
        service.record("click" if index % 2 == 0 else "hover")

    drained = service.drain()
    assert len(drained) == constants.PENDING_LIMIT
    assert all(item.kind in {"click", "hover"} for item in drained)


def test_stale_interactions_are_dropped():
    clock = FakeClock(datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    service = make_service(clock)
    service.record("click")

    clock.advance(constants.TTL_SECONDS + 1)

    assert service.drain() == ()


def test_config_defaults_to_the_bundled_values():
    assert InteractionService().config() == DEFAULT_INTERACTION_CONFIG
    assert InteractionService(MemorySettings()).config() == DEFAULT_INTERACTION_CONFIG


def test_written_config_is_the_single_source_after_a_restart():
    settings = MemorySettings()
    service = InteractionService(settings)

    saved = service.write_config({"report_enabled_kinds": ["click"], "hover_dwell_ms": 800})

    assert saved.report_enabled_kinds == ("click",)
    assert saved.hover_dwell_ms == 800
    assert saved.report_interval_ms == DEFAULT_INTERACTION_CONFIG.report_interval_ms
    assert saved.reaction_ms == DEFAULT_INTERACTION_CONFIG.reaction_ms
    assert settings.values[INTERACTION_CONFIG_KEY]
    assert InteractionService(settings).config() == saved


def test_disabled_kinds_are_reported_as_disabled_and_never_queued():
    clock = FakeClock(datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    service = InteractionService(MemorySettings(), clock=clock)
    service.write_config({"report_enabled_kinds": ["click"]})

    assert service.record("hover") == InteractionOutcome(False, "disabled")
    clock.advance(constants.THROTTLE_SECONDS)
    assert service.record("click").accepted is True
    assert [item.kind for item in service.drain()] == ["click"]


def test_illegal_patch_values_keep_the_current_config():
    settings = MemorySettings()
    service = InteractionService(settings)

    service.write_config(
        {
            "report_enabled_kinds": ["bogus", 42],
            "hover_dwell_ms": 9_999,
            "report_interval_ms": True,
            "reaction_ms": "fast",
        }
    )

    assert service.config() == DEFAULT_INTERACTION_CONFIG


def test_known_kinds_survive_a_filtered_list():
    settings = MemorySettings()
    service = InteractionService(settings)

    saved = service.write_config({"report_enabled_kinds": ["drag", "bogus"]})

    assert saved.report_enabled_kinds == ("drag",)


def test_an_empty_kind_list_turns_off_all_reporting():
    settings = MemorySettings()
    service = InteractionService(settings)

    assert service.write_config({"report_enabled_kinds": []}).report_enabled_kinds == ()
    assert service.record("click") == InteractionOutcome(False, "disabled")


def test_unreadable_stored_config_falls_back_to_defaults():
    settings = MemorySettings()
    settings.values[INTERACTION_CONFIG_KEY] = "not-json"

    assert InteractionService(settings).config() == DEFAULT_INTERACTION_CONFIG
