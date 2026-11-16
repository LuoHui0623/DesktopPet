import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent.capabilities.perception.collector.service import PerceptionService
from agent.capabilities.perception.constants import (
    MAX_AWAY_MINUTES,
    MIN_AWAY_MINUTES,
    MIN_SIT_MINUTES,
)
from agent.capabilities.perception.events.models import (
    InvalidPerceptionConfig,
    InvalidPerceptionSample,
    ProcessDecision,
)
from app.settings.coordinator import SettingsCoordinator
from app.settings.document import SettingsDocument
from infra.store.db import connect
from infra.store.migrate import migrate

NOW = datetime(2026, 10, 29, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def settings(tmp_path: Path) -> SettingsDocument:
    document = SettingsDocument(tmp_path / "settings.json")
    document.load()
    return document


def service(settings: SettingsDocument) -> PerceptionService:
    return PerceptionService(settings, clock=lambda: NOW)


def configure(settings: SettingsDocument, **values: object) -> None:
    section = dict(settings.section("perception"))
    section.update(values)
    settings.replace("perception", section)


def configure_scheduler(settings: SettingsDocument, **values: object) -> None:
    section = dict(settings.section("scheduler"))
    section.update(values)
    settings.replace("scheduler", section)


def whitelist(settings: SettingsDocument, *names: str) -> None:
    configure(settings, process_whitelist=list(names))


def at(seconds: int = 0) -> datetime:
    return NOW + timedelta(seconds=seconds)


def test_initial_state_is_enabled_but_unknown(settings: SettingsDocument):
    state = service(settings).snapshot()

    assert state.enabled is True
    assert state.foreground_enabled is True
    assert state.presence == "unknown"
    assert state.busy is False
    assert state.idle_seconds is None


def test_idle_threshold_marks_away_and_returning_is_active(settings: SettingsDocument):
    perception = service(settings)

    perception.ingest("idle", 600_000, at(0))
    assert perception.snapshot().presence == "away"

    perception.ingest("idle", 120_000, at(10))
    assert perception.snapshot().presence == "active"


def test_busy_covers_fullscreen_and_locked(settings: SettingsDocument):
    perception = service(settings)

    perception.ingest("fullscreen", True, at(0))
    assert perception.snapshot().busy is True

    perception.ingest("fullscreen", False, at(10))
    perception.ingest("locked", True, at(11))
    assert perception.snapshot().busy is True

    perception.ingest("locked", False, at(20))
    assert perception.snapshot().busy is False


def test_foreground_outside_whitelist_is_dropped(settings: SettingsDocument):
    perception = service(settings)

    outcome = perception.ingest("foreground", "chrome", at(0))

    assert outcome.accepted is False
    assert perception.snapshot().foreground is None

    # 名单在段快照里：首读之前改段仍会影响服务（真源是段 → 领域状态的初值）。
    whitelist(settings, "code")
    perception = service(settings)
    perception.ingest("foreground", "Code.EXE", at(10))

    assert perception.snapshot().foreground == "code"


def test_candidates_are_capped_and_decisions_project_into_the_section(
    settings: SettingsDocument,
):
    perception = service(settings)
    for index in range(21):
        perception.ingest("process_candidate", f"app{index}", at(index))

    state = perception.snapshot()
    assert len(state.candidates) == 20
    assert state.candidates[0] == "app1"
    assert state.candidates[-1] == "app20"

    state = perception.decide_process("APP20.EXE", ProcessDecision.ALLOW)
    assert state.whitelist == ("app20",)
    assert "app20" not in state.candidates
    # 名单也是配置：领域状态改完即可投射到 `perception` 段。
    assert perception.snapshot_settings()["process_whitelist"] == ["app20"]

    state = perception.decide_process("app20", ProcessDecision.IGNORE)
    assert state.whitelist == ()
    assert state.muted == ("app20",)
    assert perception.ingest("process_candidate", "app20", at(30)).accepted is False


def test_ingesting_candidates_never_touches_the_snapshot(settings: SettingsDocument):
    perception = service(settings)
    before = settings.section("perception")

    perception.ingest("process_candidate", "chrome", at(0))

    assert settings.section("perception") == before
    assert perception.snapshot().candidates == ("chrome",)


def test_same_kind_and_value_within_five_seconds_keeps_one(settings: SettingsDocument):
    whitelist(settings, "code")
    perception = service(settings)

    assert perception.ingest("foreground", "code", at(0)).accepted is True
    duplicate = perception.ingest("foreground", "code", at(4))

    assert duplicate.accepted is False
    assert duplicate.reason == "duplicate"


def test_disabled_switch_drops_samples(settings: SettingsDocument):
    perception = service(settings)
    perception.ingest("idle", 10_000, at(0))
    perception.ingest("fullscreen", True, at(1))
    perception.write_config(
        enabled=False,
        foreground_enabled=True,
        idle_away_minutes=10,
        sit_minutes=90,
    )

    outcome = perception.ingest("idle", 20_000, at(10))
    state = perception.snapshot()

    assert outcome.reason == "disabled"
    assert state.enabled is False
    assert state.presence == "unknown"
    assert state.busy is False


def test_foreground_switch_only_drops_process_kinds(settings: SettingsDocument):
    configure(settings, foreground_enabled=False)
    whitelist(settings, "code")
    perception = service(settings)

    assert perception.ingest("idle", 1_000, at(0)).accepted is True
    assert perception.ingest("foreground", "code", at(1)).reason == "foreground_disabled"
    assert perception.ingest("processes", ["code"], at(2)).reason == "foreground_disabled"
    assert perception.ingest("process_candidate", "chrome", at(3)).reason == "foreground_disabled"


def test_invalid_values_are_rejected(settings: SettingsDocument):
    perception = service(settings)

    with pytest.raises(InvalidPerceptionSample):
        perception.ingest("idle", -1, at(0))
    with pytest.raises(InvalidPerceptionSample):
        perception.ingest("fullscreen", "true", at(0))
    with pytest.raises(InvalidPerceptionSample):
        perception.ingest("foreground", "../chrome", at(0))


def test_derived_events_and_triggers_stay_out_of_daily_log(
    db_file: Path, settings: SettingsDocument
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


def test_untriggered_events_reach_context_once(db_file: Path, settings: SettingsDocument):
    whitelist(settings, "code")
    configure_scheduler(settings, enabled=False)
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


def test_ingesting_samples_never_writes_daily_log(db_file: Path, settings: SettingsDocument):
    whitelist(settings, "code")
    perception = service(settings)

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


def test_write_config_only_changes_domain_state(settings: SettingsDocument):
    """域方法不落盘（`R8`）：写入后段快照不变，投射与持久化由协调器负责。"""
    perception = service(settings)
    before = settings.section("perception")

    saved = perception.write_config(
        enabled=True,
        foreground_enabled=False,
        idle_away_minutes=20,
        sit_minutes=45,
    )

    assert saved.enabled is True
    assert saved.foreground_enabled is False
    assert saved.idle_away_minutes == 20
    assert settings.section("perception") == before
    assert perception.snapshot_settings()["idle_away_minutes"] == 20

    coordinator = SettingsCoordinator(settings, {"perception": perception.snapshot_settings})
    coordinator.save("perception", version=settings.version)

    reloaded = SettingsDocument(settings.path)
    reloaded.load()
    assert reloaded.section("perception")["idle_away_minutes"] == 20
    assert reloaded.section("perception")["sit_minutes"] == 45


def test_write_config_takes_effect_without_a_restart(settings: SettingsDocument):
    perception = service(settings)
    perception.ingest("idle", 90_000, at(0))
    assert perception.snapshot().presence == "active"

    perception.write_config(
        enabled=True,
        foreground_enabled=True,
        idle_away_minutes=1,
        sit_minutes=90,
    )

    assert perception.snapshot().presence == "away"


def test_turning_perception_off_with_config_clears_the_buffer(settings: SettingsDocument):
    perception = service(settings)
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


def test_write_config_rejects_out_of_range_thresholds(settings: SettingsDocument):
    perception = service(settings)
    before = settings.section("perception")

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

    # 校验失败不改领域状态，也不写快照。
    assert perception.config().enabled is True
    assert settings.section("perception") == before


def test_write_config_keeps_the_process_lists(settings: SettingsDocument):
    whitelist(settings, "code")
    configure(settings, process_muted=["chrome"])
    perception = service(settings)

    saved = perception.write_config(
        enabled=False,
        foreground_enabled=True,
        idle_away_minutes=15,
        sit_minutes=60,
    )

    assert saved.whitelist == ("code",)
    assert saved.muted == ("chrome",)
