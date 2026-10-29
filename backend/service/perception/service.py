import re
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from module.settings.settings_service import SettingsService
from service.perception.constants import (
    CANDIDATE_LIMIT,
    CONTEXT_EVENT_LIMIT,
    DEDUP_SECONDS,
    KIND_FOREGROUND,
    KIND_FULLSCREEN,
    KIND_IDLE,
    KIND_LOCKED,
    KIND_PROCESSES,
    KINDS,
    PROCESS_KINDS,
    PROCESS_MUTED_KEY,
    PROCESS_WHITELIST_KEY,
)
from service.perception.events import PerceptionEvent, Trigger
from service.perception.models import (
    IngestOutcome,
    InvalidPerceptionSample,
    PerceptionRuntimeState,
    PerceptionSnapshot,
    ProcessDecision,
)
from service.perception.parser import PerceptionEventParser
from service.perception.scheduler import (
    SchedulerLedger,
    SchedulerLedgerService,
    SchedulerRuntime,
    SchedulerSettings,
)
from service.perception.settings import PerceptionConfig, PerceptionSettings

DEFAULT_TIMEZONE = ZoneInfo("Asia/Shanghai")

EXE_NAME = re.compile(r"[a-z0-9\u4e00-\u9fff][a-z0-9\u4e00-\u9fff ._-]*")
INVALID_EXE_CHARS = set('/\\:*?"<>|')


def normalize_exe(raw: str) -> str | None:
    name = raw.strip().lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if not name or len(name) > 64 or name in {".", ".."}:
        return None
    if INVALID_EXE_CHARS & set(name):
        return None
    return name if EXE_NAME.fullmatch(name) else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise InvalidPerceptionSample("at 必须携带时区")
    return value.astimezone(UTC)


def _value_key(value: int | bool | str | list[str] | None) -> tuple:
    if isinstance(value, list):
        return ("list", tuple(value))
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, str):
        return ("str", value)
    return ("none",)


class PerceptionService:
    def __init__(
        self,
        settings: SettingsService,
        clock: Callable[[], datetime] | None = None,
        timezone: ZoneInfo | None = None,
        chat_active: Callable[[], bool] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        zone = timezone or DEFAULT_TIMEZONE
        self._settings = PerceptionSettings(settings)
        self._scheduler = SchedulerRuntime(
            SchedulerSettings(settings),
            SchedulerLedgerService(settings, zone),
            timezone=zone,
            clock=lambda _: self._clock(),
            chat_active=chat_active,
        )
        self._parser = PerceptionEventParser(zone, scheduler=self._scheduler)
        self._lock = threading.RLock()
        self._idle_ms: int | None = None
        self._foreground: str | None = None
        self._fullscreen = False
        self._locked = False
        self._processes: tuple[str, ...] = ()
        self._candidates: deque[str] = deque()
        self._last: dict[str, tuple[tuple, datetime]] = {}

    def ingest(
        self,
        kind: str,
        value: int | bool | str | list[str] | None,
        at: datetime,
    ) -> IngestOutcome:
        timestamp = _as_utc(at)
        if kind not in KINDS:
            raise InvalidPerceptionSample(f"不支持的感知类型：{kind}")
        normalized = _validate_value(kind, value)
        with self._lock:
            config = self._settings.read()
            if not config.enabled:
                return self._outcome(False, "disabled")
            if kind in PROCESS_KINDS and not config.foreground_enabled:
                return self._outcome(False, "foreground_disabled")
            if self._is_duplicate(kind, normalized, timestamp):
                return self._outcome(False, "duplicate")
            if kind == KIND_IDLE:
                self._apply_idle(normalized)
                accepted = True
            elif kind == KIND_FOREGROUND:
                accepted = self._apply_foreground(normalized, config)
            elif kind == KIND_FULLSCREEN:
                self._apply_flag("fullscreen", normalized)
                accepted = True
            elif kind == KIND_LOCKED:
                self._apply_flag("locked", normalized)
                accepted = True
            elif kind == KIND_PROCESSES:
                self._apply_processes(normalized, config)
                accepted = True
            else:
                outcome = self._apply_candidate(normalized, config)
                if not outcome.accepted:
                    return outcome
                accepted = True
            self._parser.observe(self._runtime_state(config), timestamp)
            if not accepted:
                return self._outcome(False, "not_whitelisted")
            self._last[kind] = (_value_key(normalized), timestamp)
            return self._outcome(True, None)

    def snapshot(self) -> PerceptionSnapshot:
        with self._lock:
            return self._snapshot(self._settings.read())

    def config(self) -> PerceptionConfig:
        with self._lock:
            return self._settings.read()

    def write_config(
        self,
        *,
        enabled: bool,
        foreground_enabled: bool,
        idle_away_minutes: int,
        sit_minutes: int,
    ) -> PerceptionConfig:
        """只写开关与阈值；白名单与忽略名单仍由进程决策接口维护。"""
        with self._lock:
            current = self._settings.read()
            self._settings.write(
                PerceptionConfig(
                    enabled=enabled,
                    foreground_enabled=foreground_enabled,
                    idle_away_minutes=idle_away_minutes,
                    sit_minutes=sit_minutes,
                    whitelist=current.whitelist,
                    muted=current.muted,
                )
            )
            saved = self._settings.read()
            if not saved.enabled:
                self.clear()
            else:
                self._parser.observe(self._runtime_state(saved), self._utc_now())
            return saved

    def tick(self) -> tuple[Trigger, ...]:
        with self._lock:
            config = self._settings.read()
            if not config.enabled:
                self._parser.reset()
                return ()
            return self._parser.tick(self._utc_now(), config)

    def recent_context_events(
        self, limit: int = CONTEXT_EVENT_LIMIT
    ) -> tuple[PerceptionEvent, ...]:
        with self._lock:
            if not self._settings.read().enabled:
                return ()
            return self._parser.take_context_events(limit)

    @property
    def scheduler(self) -> SchedulerRuntime:
        return self._scheduler

    def record_sent(self, topic: str, at: datetime | None = None) -> SchedulerLedger:
        return self._scheduler.record_sent(topic, at)

    def decide_process(self, exe: str, decision: ProcessDecision) -> PerceptionSnapshot:
        name = normalize_exe(exe)
        if name is None:
            raise InvalidPerceptionSample("进程名不合法")
        with self._lock:
            config = self._settings.read()
            whitelist = [item for item in config.whitelist if item != name]
            muted = [item for item in config.muted if item != name]
            if decision is ProcessDecision.ALLOW:
                whitelist.append(name)
            else:
                muted.append(name)
            self._settings.write_names(PROCESS_WHITELIST_KEY, tuple(whitelist))
            self._settings.write_names(PROCESS_MUTED_KEY, tuple(muted))
            self._remove_candidate(name)
            if decision is ProcessDecision.IGNORE and self._foreground == name:
                self._foreground = None
            config = self._settings.read()
            self._parser.observe(self._runtime_state(config), self._utc_now())
            return self._snapshot(config)

    def clear(self) -> None:
        """丢弃内存里的感知缓冲：采样状态、候选进程与去重窗口；白名单等配置不受影响。"""
        with self._lock:
            self._last.clear()
            self._candidates.clear()
            self._idle_ms = None
            self._foreground = None
            self._fullscreen = False
            self._locked = False
            self._processes = ()
            self._parser.reset()

    def _apply_idle(self, value: int) -> None:
        self._idle_ms = value

    def _apply_foreground(self, value: str | None, config: PerceptionConfig) -> bool:
        if value is not None and value not in config.whitelist:
            self._foreground = None
            return False
        self._foreground = value
        return True

    def _apply_flag(self, kind: str, value: bool) -> None:
        if kind == "fullscreen":
            self._fullscreen = value
        else:
            self._locked = value

    def _apply_processes(self, value: list[str], config: PerceptionConfig) -> None:
        self._processes = tuple(sorted({name for name in value if name in config.whitelist}))

    def _apply_candidate(self, value: str, config: PerceptionConfig) -> IngestOutcome:
        if value in config.whitelist or value in config.muted:
            return self._outcome(False, "known_process")
        self._remove_candidate(value)
        self._candidates.append(value)
        while len(self._candidates) > CANDIDATE_LIMIT:
            self._candidates.popleft()
        return self._outcome(True, None)

    def _remove_candidate(self, name: str) -> None:
        try:
            self._candidates.remove(name)
        except ValueError:
            pass

    def _is_duplicate(
        self,
        kind: str,
        value: int | bool | str | list[str] | None,
        at: datetime,
    ) -> bool:
        previous = self._last.get(kind)
        if previous is None:
            return False
        key, previous_at = previous
        return key == _value_key(value) and at - previous_at < timedelta(seconds=DEDUP_SECONDS)

    def _runtime_state(self, config: PerceptionConfig) -> PerceptionRuntimeState:
        return PerceptionRuntimeState(
            presence=self._presence(config),
            busy=self._fullscreen or self._locked,
            idle_ms=self._idle_ms,
            foreground=self._foreground if config.foreground_enabled else None,
            processes=self._processes,
        )

    def _presence(self, config: PerceptionConfig) -> str:
        if self._idle_ms is None:
            return "unknown"
        threshold = config.idle_away_minutes * 60 * 1000
        return "away" if self._idle_ms >= threshold else "active"

    def _snapshot(self, config: PerceptionConfig) -> PerceptionSnapshot:
        enabled = config.enabled
        idle_ms = self._idle_ms if enabled else None
        presence = self._presence(config) if enabled else "unknown"
        return PerceptionSnapshot(
            enabled=enabled,
            foreground_enabled=config.foreground_enabled,
            presence=presence,
            busy=enabled and (self._fullscreen or self._locked),
            idle_seconds=None if idle_ms is None else idle_ms // 1000,
            foreground=self._foreground if enabled and config.foreground_enabled else None,
            whitelist=config.whitelist,
            muted=config.muted,
            candidates=tuple(self._candidates),
        )

    def _outcome(self, accepted: bool, reason: str | None) -> IngestOutcome:
        return IngestOutcome(accepted=accepted, reason=reason, candidates=tuple(self._candidates))

    def _utc_now(self) -> datetime:
        return _as_utc(self._clock())


def _validate_value(
    kind: str, value: int | bool | str | list[str] | None
) -> int | bool | str | list[str] | None:
    if kind == KIND_IDLE:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidPerceptionSample("idle 必须是非负整数毫秒")
        return value
    if kind in {KIND_FULLSCREEN, KIND_LOCKED}:
        if not isinstance(value, bool):
            raise InvalidPerceptionSample(f"{kind} 必须是布尔值")
        return value
    if kind == KIND_FOREGROUND:
        if value is None:
            return None
        if not isinstance(value, str):
            raise InvalidPerceptionSample("foreground 必须是进程名或 null")
        return _require_exe(value)
    if kind == KIND_PROCESSES:
        if not isinstance(value, list):
            raise InvalidPerceptionSample("processes 必须是进程名数组")
        return sorted({_require_exe(item) for item in value})
    if not isinstance(value, str):
        raise InvalidPerceptionSample("process_candidate 必须是进程名")
    return _require_exe(value)


def _require_exe(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidPerceptionSample("进程名必须是字符串")
    normalized = normalize_exe(value)
    if normalized is None:
        raise InvalidPerceptionSample("进程名不合法")
    return normalized
