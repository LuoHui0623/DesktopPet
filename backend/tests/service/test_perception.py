import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.settings.settings_service import SettingsService
from service.perception import (
    InvalidPerceptionConfig,
    InvalidPerceptionSample,
    PerceptionService,
    ProcessDecision,
)
from service.perception.constants import (
    MAX_AWAY_MINUTES,
    MIN_AWAY_MINUTES,
    MIN_SIT_MINUTES,
    PROCESS_MUTED_KEY,
    PROCESS_WHITELIST_KEY,
)

NOW = datetime(2026, 10, 29, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def settings(db_file: Path) -> SettingsService:
    return SettingsService(db_file)


def service(db_file: Path, settings: SettingsService) -> PerceptionService:
    return PerceptionService(settings, clock=lambda: NOW)


def whitelist(settings: SettingsService, *names: str) -> None:
    settings.put(PROCESS_WHITELIST_KEY, json.dumps(list(names)))


def at(seconds: int = 0) -> datetime:
    return NOW + timedelta(seconds=seconds)


def test_initial_state_is_enabled_but_unknown(db_file: Path, settings: SettingsService):
    state = service(db_file, settings).snapshot()

    assert state.enabled is True
    assert state.foreground_enabled is True
    assert state.presence == "unknown"
    assert state.busy is False
    assert state.idle_seconds is None


def test_idle_threshold_marks_away_and_returning_is_active(
    db_file: Path, settings: SettingsService
):
    perception = service(db_file, settings)

    perception.ingest("idle", 600_000, at(0))
    assert perception.snapshot().presence == "away"

    perception.ingest("idle", 120_000, at(10))
    assert perception.snapshot().presence == "active"


def test_busy_covers_fullscreen_and_locked(db_file: Path, settings: SettingsService):
    perception = service(db_file, settings)

    perception.ingest("fullscreen", True, at(0))
    assert perception.snapshot().busy is True

    perception.ingest("fullscreen", False, at(10))
    perception.ingest("locked", True, at(11))
    assert perception.snapshot().busy is True

    perception.ingest("locked", False, at(20))
    assert perception.snapshot().busy is False


def test_foreground_outside_whitelist_is_dropped(db_file: Path, settings: SettingsService):
    perception = service(db_file, settings)

    outcome = perception.ingest("foreground", "chrome", at(0))

    assert outcome.accepted is False
    assert perception.snapshot().foreground is None

    whitelist(settings, "code")
    perception.ingest("foreground", "Code.EXE", at(10))

    assert perception.snapshot().foreground == "code"


def test_candidates_are_capped_and_decisions_persist(db_file: Path, settings: SettingsService):
    perception = service(db_file, settings)
    for index in range(21):
        perception.ingest("process_candidate", f"app{index}", at(index))

    state = perception.snapshot()
    assert len(state.candidates) == 20
    assert state.candidates[0] == "app1"
    assert state.candidates[-1] == "app20"

    state = perception.decide_process("APP20.EXE", ProcessDecision.ALLOW)
    assert state.whitelist == ("app20",)
    assert "app20" not in state.candidates
    assert json.loads(settings.get(PROCESS_WHITELIST_KEY) or "[]") == ["app20"]

    state = perception.decide_process("app20", ProcessDecision.IGNORE)
    assert state.whitelist == ()
    assert state.muted == ("app20",)
    assert perception.ingest("process_candidate", "app20", at(30)).accepted is False


def test_candidates_stay_in_memory(db_file: Path, settings: SettingsService):
    perception = service(db_file, settings)

    def setting_count() -> int:
        conn = sqlite3.connect(db_file)
        try:
            return conn.execute("SELECT COUNT(*) FROM app_setting").fetchone()[0]
        finally:
            conn.close()

    before = setting_count()
    perception.ingest("process_candidate", "chrome", at(0))

    assert setting_count() == before
    assert perception.snapshot().candidates == ("chrome",)


def test_same_kind_and_value_within_five_seconds_keeps_one(
    db_file: Path, settings: SettingsService
):
    whitelist(settings, "code")
    perception = service(db_file, settings)

    assert perception.ingest("foreground", "code", at(0)).accepted is True
    duplicate = perception.ingest("foreground", "code", at(4))

    assert duplicate.accepted is False
    assert duplicate.reason == "duplicate"


def test_disabled_switch_drops_samples(db_file: Path, settings: SettingsService):
    perception = service(db_file, settings)
    perception.ingest("idle", 10_000, at(0))
    perception.ingest("fullscreen", True, at(1))
    settings.put("perception.enabled", "false")

    outcome = perception.ingest("idle", 20_000, at(10))
    state = perception.snapshot()

    assert outcome.reason == "disabled"
    assert state.enabled is False
    assert state.presence == "unknown"
    assert state.busy is False


def test_foreground_switch_only_drops_process_kinds(db_file: Path, settings: SettingsService):
    settings.put("perception.foreground_enabled", "false")
    whitelist(settings, "code")
    perception = service(db_file, settings)

    assert perception.ingest("idle", 1_000, at(0)).accepted is True
    assert perception.ingest("foreground", "code", at(1)).reason == "foreground_disabled"
    assert perception.ingest("processes", ["code"], at(2)).reason == "foreground_disabled"
    assert perception.ingest("process_candidate", "chrome", at(3)).reason == "foreground_disabled"


def test_invalid_values_are_rejected(db_file: Path, settings: SettingsService):
    perception = service(db_file, settings)

    with pytest.raises(InvalidPerceptionSample):
        perception.ingest("idle", -1, at(0))
    with pytest.raises(InvalidPerceptionSample):
        perception.ingest("fullscreen", "true", at(0))
    with pytest.raises(InvalidPerceptionSample):
        perception.ingest("foreground", "../chrome", at(0))


def test_derived_events_and_triggers_stay_out_of_daily_log(
    db_file: Path, settings: SettingsService
):
    whitelist(settings, "code")
    now = [NOW]
    perception = PerceptionService(
        settings,
        clock=lambda: now[0],
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    perception.ingest("idle", 0, at(0))
    perception.ingest("foreground", "code", at(1))
    now[0] = at(181)

    triggers = perception.tick()

    assert [trigger.topic for trigger in triggers] == ["app_in_use.code"]
    assert triggers[0].reason == "使用 code 已 3 分钟"
    assert perception.recent_context_events() == ()
    assert not (db_file.parent / "memory").exists()
    conn = sqlite3.connect(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM daily_log").fetchone()[0] == 0
    finally:
        conn.close()


def test_untriggered_events_reach_context_once(db_file: Path, settings: SettingsService):
    whitelist(settings, "code")
    settings.put("scheduler.enabled", "false")
    now = [NOW]
    perception = PerceptionService(
        settings,
        clock=lambda: now[0],
        timezone=ZoneInfo("Asia/Shanghai"),
    )
    perception.ingest("idle", 0, at(0))
    perception.ingest("foreground", "code", at(1))
    now[0] = at(181)

    assert perception.tick() == ()
    events = perception.recent_context_events()

    assert [event.kind.value for event in events] == ["app_in_use"]
    assert perception.recent_context_events() == ()


def test_ingesting_samples_never_writes_daily_log(db_file: Path, settings: SettingsService):
    whitelist(settings, "code")
    perception = service(db_file, settings)

    perception.ingest("idle", 100_000, at(0))
    perception.ingest("foreground", "code", at(1))
    perception.ingest("fullscreen", True, at(2))
    perception.ingest("processes", ["code"], at(3))
    perception.ingest("process_candidate", "chrome", at(4))
    perception.decide_process("chrome", ProcessDecision.ALLOW)

    conn = sqlite3.connect(db_file)
    try:
        count = conn.execute("SELECT COUNT(*) FROM daily_log").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_write_config_persists_and_reloads(db_file: Path, settings: SettingsService):
    perception = service(db_file, settings)

    saved = perception.write_config(
        enabled=True,
        foreground_enabled=False,
        idle_away_minutes=20,
        sit_minutes=45,
    )

    assert saved.enabled is True
    assert saved.foreground_enabled is False
    assert saved.idle_away_minutes == 20
    assert saved.sit_minutes == 45
    assert settings.get("perception.idle_away_minutes") == "20"
    assert settings.get("perception.sit_minutes") == "45"
    assert service(db_file, settings).config() == saved


def test_write_config_takes_effect_without_a_restart(db_file: Path, settings: SettingsService):
    perception = service(db_file, settings)
    perception.ingest("idle", 90_000, at(0))
    assert perception.snapshot().presence == "active"

    perception.write_config(
        enabled=True,
        foreground_enabled=True,
        idle_away_minutes=1,
        sit_minutes=90,
    )

    assert perception.snapshot().presence == "away"


def test_turning_perception_off_with_config_clears_the_buffer(
    db_file: Path, settings: SettingsService
):
    perception = service(db_file, settings)
    perception.ingest("idle", 600_000, at(0))
    perception.ingest("fullscreen", True, at(1))
    perception.ingest("process_candidate", "chrome", at(2))

    perception.write_config(
        enabled=False,
        foreground_enabled=True,
        idle_away_minutes=10,
        sit_minutes=90,
    )

    state = perception.snapshot()
    assert state.enabled is False
    assert state.presence == "unknown"
    assert state.busy is False
    assert state.idle_seconds is None
    assert state.candidates == ()
    assert perception.recent_context_events() == ()
    assert perception.ingest("idle", 1_000, at(3)).reason == "disabled"


def test_write_config_rejects_out_of_range_thresholds(db_file: Path, settings: SettingsService):
    perception = service(db_file, settings)

    for minutes in (MIN_AWAY_MINUTES - 1, MAX_AWAY_MINUTES + 1):
        with pytest.raises(InvalidPerceptionConfig):
            perception.write_config(
                enabled=True,
                foreground_enabled=True,
                idle_away_minutes=minutes,
                sit_minutes=90,
            )
    with pytest.raises(InvalidPerceptionConfig):
        perception.write_config(
            enabled=True,
            foreground_enabled=True,
            idle_away_minutes=10,
            sit_minutes=MIN_SIT_MINUTES - 1,
        )

    assert settings.get("perception.enabled") is None


def test_write_config_keeps_the_process_lists(db_file: Path, settings: SettingsService):
    whitelist(settings, "code")
    settings.put(PROCESS_MUTED_KEY, json.dumps(["chrome"]))
    perception = service(db_file, settings)

    saved = perception.write_config(
        enabled=False,
        foreground_enabled=True,
        idle_away_minutes=15,
        sit_minutes=60,
    )

    assert saved.whitelist == ("code",)
    assert saved.muted == ("chrome",)
