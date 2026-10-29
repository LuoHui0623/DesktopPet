import logging
from datetime import date, datetime, timedelta, tzinfo

from service.perception.constants import (
    APP_IN_USE_MINUTES,
    APP_LAUNCH_CONFIRM_SECONDS,
    APP_QUIT_MIN_MINUTES,
    BUSY_ENTER_SECONDS,
    BUSY_RELEASE_SECONDS,
    BUSY_RELEASED_MIN_MINUTES,
    DEFAULT_TOPIC_DAILY_LIMIT,
    GREETING_MIN_AWAY_MINUTES,
    LATE_NIGHT_END_HOUR,
    LATE_NIGHT_START_HOUR,
    LATE_NIGHT_TOPIC,
    SIT_REPEAT_MINUTES,
    TOPIC_DAILY_LIMITS,
)
from service.perception.events import PerceptionEvent, PerceptionEventKind, Trigger
from service.perception.models import PerceptionRuntimeState
from service.perception.scheduler import SchedulerRuntime, in_quiet_hours, is_deep_night
from service.perception.settings import PerceptionConfig

logger = logging.getLogger("pet.perception")


def _seconds(value: float) -> timedelta:
    return timedelta(seconds=value)


class PerceptionEventParser:
    def __init__(
        self,
        timezone: tzinfo,
        *,
        scheduler: SchedulerRuntime,
    ) -> None:
        self._timezone = timezone
        self._scheduler = scheduler
        self._current: list[PerceptionEvent] = []
        self._pending: list[Trigger] = []
        self.reset()

    def reset(self) -> None:
        self._current.clear()
        self._pending.clear()
        self._presence = "unknown"
        self._idle_ms: int | None = None
        self._away_since: datetime | None = None
        self._sit_anchor: datetime | None = None
        self._sit_last_at: datetime | None = None
        self._raw_busy = False
        self._busy_since: datetime | None = None
        self._busy_released_since: datetime | None = None
        self._busy_emitted = False
        self._foreground: str | None = None
        self._foreground_since: datetime | None = None
        self._in_use: set[str] = set()
        self._processes: dict[str, datetime] = {}
        self._launch_pending: dict[str, datetime] = {}
        self._processes_seen = False
        self._late_night_event_key: date | None = None
        self._late_night_trigger_key: date | None = None
        self._last_denial_log: tuple[str, str] | None = None

    def observe(self, state: PerceptionRuntimeState, at: datetime) -> None:
        self._idle_ms = state.idle_ms
        self._sync_presence(state.presence, at)
        self._sync_busy(state.busy, at)
        self._sync_foreground(state.foreground, at)
        self._sync_processes(state.processes, at)

    def tick(self, now: datetime, config: PerceptionConfig) -> tuple[Trigger, ...]:
        self._check_busy(now)
        self._check_sit(now, config)
        self._check_foreground(now)
        self._check_launches(now)
        self._check_late_night(now)
        pending = tuple(self._pending)
        self._pending.clear()
        if pending:
            self._current.clear()
        return pending

    def current_events(self, limit: int | None = None) -> tuple[PerceptionEvent, ...]:
        items = self._current
        return tuple(items[-limit:] if limit else items)

    def take_context_events(self, limit: int) -> tuple[PerceptionEvent, ...]:
        kept: list[PerceptionEvent] = []
        seen_in_use = False
        for event in reversed(self._current):
            if not event.in_context:
                continue
            if event.kind is PerceptionEventKind.APP_IN_USE:
                if seen_in_use:
                    continue
                seen_in_use = True
            kept.append(event)
            if len(kept) == limit:
                break
        self._current.clear()
        return tuple(reversed(kept))

    def _sync_presence(self, presence: str, at: datetime) -> None:
        if presence == self._presence:
            return
        previous = self._presence
        self._presence = presence
        if presence == "away":
            idle = self._idle_ms or 0
            self._away_since = at - timedelta(milliseconds=idle)
            self._sit_anchor = None
            self._sit_last_at = None
            self._record(
                PerceptionEvent(PerceptionEventKind.PRESENCE_AWAY, at, minutes=idle // 60_000)
            )
            return
        if presence != "active":
            return
        if previous == "away" and self._away_since is not None:
            minutes = max(int((at - self._away_since).total_seconds() // 60), 0)
            self._record(PerceptionEvent(PerceptionEventKind.PRESENCE_RETURN, at, minutes=minutes))
            self._sit_anchor = at
        else:
            idle = self._idle_ms or 0
            self._sit_anchor = at - timedelta(milliseconds=idle)
        self._away_since = None
        self._sit_last_at = None

    def _sync_busy(self, raw: bool, at: datetime) -> None:
        if raw == self._raw_busy:
            return
        self._raw_busy = raw
        if raw:
            if self._busy_since is None or not self._busy_emitted:
                self._busy_since = at
                self._busy_emitted = False
            self._busy_released_since = None
        elif self._busy_since is not None:
            self._busy_released_since = at

    def _sync_foreground(self, foreground: str | None, at: datetime) -> None:
        if foreground == self._foreground:
            return
        if self._foreground is not None:
            self._in_use.discard(self._foreground)
        self._foreground = foreground
        self._foreground_since = at if foreground is not None else None

    def _sync_processes(self, names: tuple[str, ...], at: datetime) -> None:
        current = set(names)
        if not self._processes_seen:
            self._processes = {name: at for name in current}
            self._processes_seen = True
            return
        known = set(self._processes)
        for name in sorted(current - known):
            self._processes[name] = at
            self._launch_pending[name] = at
        for name in sorted(known - current):
            since = self._processes.pop(name)
            self._launch_pending.pop(name, None)
            minutes = max(int((at - since).total_seconds() // 60), 0)
            self._record(
                PerceptionEvent(PerceptionEventKind.APP_QUIT, at, exe=name, minutes=minutes)
            )

    def _check_busy(self, now: datetime) -> None:
        if self._raw_busy:
            if (
                self._busy_since is not None
                and not self._busy_emitted
                and now - self._busy_since >= _seconds(BUSY_ENTER_SECONDS)
            ):
                self._record(PerceptionEvent(PerceptionEventKind.BUSY_ENTER, self._busy_since))
                self._busy_emitted = True
            return
        if self._busy_since is None or self._busy_released_since is None:
            return
        if now - self._busy_released_since < _seconds(BUSY_RELEASE_SECONDS):
            return
        if self._busy_emitted:
            minutes = max(
                int((self._busy_released_since - self._busy_since).total_seconds() // 60), 0
            )
            self._record(
                PerceptionEvent(
                    PerceptionEventKind.BUSY_EXIT,
                    self._busy_released_since,
                    minutes=minutes,
                ),
                speak_at=now,
            )
        self._busy_since = None
        self._busy_released_since = None
        self._busy_emitted = False

    def _check_sit(self, now: datetime, config: PerceptionConfig) -> None:
        if self._presence != "active" or self._sit_anchor is None:
            return
        if self._sit_last_at is None:
            if now - self._sit_anchor < timedelta(minutes=config.sit_minutes):
                return
        elif now - self._sit_last_at < timedelta(minutes=SIT_REPEAT_MINUTES):
            return
        minutes = max(int((now - self._sit_anchor).total_seconds() // 60), 0)
        self._record(PerceptionEvent(PerceptionEventKind.SIT_LONG, now, minutes=minutes))
        self._sit_last_at = now

    def _check_foreground(self, now: datetime) -> None:
        if self._foreground is None or self._foreground in self._in_use:
            return
        if self._foreground_since is None:
            return
        if now - self._foreground_since < timedelta(minutes=APP_IN_USE_MINUTES):
            return
        minutes = max(int((now - self._foreground_since).total_seconds() // 60), 0)
        self._record(
            PerceptionEvent(
                PerceptionEventKind.APP_IN_USE,
                now,
                exe=self._foreground,
                minutes=minutes,
            )
        )
        self._in_use.add(self._foreground)

    def _check_launches(self, now: datetime) -> None:
        for name, since in list(self._launch_pending.items()):
            if name not in self._processes:
                del self._launch_pending[name]
                continue
            if now - since < _seconds(APP_LAUNCH_CONFIRM_SECONDS):
                continue
            self._record(
                PerceptionEvent(PerceptionEventKind.APP_LAUNCHED, since, exe=name),
                speak_at=now,
            )
            del self._launch_pending[name]

    def _check_late_night(self, now: datetime) -> None:
        if self._presence != "active":
            return
        local = now.astimezone(self._timezone)
        hour = local.hour
        if hour >= LATE_NIGHT_START_HOUR:
            night = local.date()
        elif hour < LATE_NIGHT_END_HOUR:
            night = local.date() - timedelta(days=1)
        else:
            return
        if self._late_night_event_key != night:
            self._record(PerceptionEvent(PerceptionEventKind.LATE_NIGHT, now), trigger=False)
            self._late_night_event_key = night
        if hour >= LATE_NIGHT_END_HOUR or self._late_night_trigger_key == night:
            return
        if self._queue("late_night", "深夜仍在使用电脑", now):
            self._late_night_trigger_key = night

    def denial_reason(self, topic: str, at: datetime) -> str | None:
        if self._presence != "active":
            return "away"
        if self._debounced_busy(at):
            return "busy"
        config = self._scheduler.config()
        if not config.enabled:
            return "scheduler_disabled"
        if self._scheduler.chatting:
            return "chatting"
        local = at.astimezone(self._timezone)
        deep_night = is_deep_night(local)
        # 深夜窗口只为 late_night 放行，否则默认勿扰会覆盖整个深夜，该主题永远发不出
        if in_quiet_hours(local, config) and not (deep_night and topic == LATE_NIGHT_TOPIC):
            return "quiet_hours"
        if deep_night and topic != LATE_NIGHT_TOPIC:
            return "deep_night"
        last_sent = self._scheduler.last_sent_at
        if last_sent is not None and at - last_sent < timedelta(
            minutes=config.min_interval_minutes
        ):
            return "min_interval"
        ledger = self._scheduler.ledger(at)
        if ledger.total >= config.max_per_day:
            return "daily_limit"
        limit = TOPIC_DAILY_LIMITS.get(topic, DEFAULT_TOPIC_DAILY_LIMIT)
        if ledger.topics.get(topic, 0) >= limit:
            return "topic_limit"
        return None

    def _debounced_busy(self, now: datetime) -> bool:
        if self._raw_busy:
            return True
        if self._busy_emitted and self._busy_released_since is not None:
            return now - self._busy_released_since < _seconds(BUSY_RELEASE_SECONDS)
        return False

    def _record(
        self,
        event: PerceptionEvent,
        *,
        trigger: bool = True,
        speak_at: datetime | None = None,
    ) -> None:
        self._current.append(event)
        if not trigger:
            return
        topic = event.topic
        if topic is None or not self._topic_allowed(event):
            return
        self._queue(topic, event.summary, speak_at or event.at)

    def _topic_allowed(self, event: PerceptionEvent) -> bool:
        minutes = event.minutes or 0
        if event.kind is PerceptionEventKind.PRESENCE_RETURN:
            return minutes >= GREETING_MIN_AWAY_MINUTES
        if event.kind is PerceptionEventKind.BUSY_EXIT:
            return minutes >= BUSY_RELEASED_MIN_MINUTES
        if event.kind is PerceptionEventKind.APP_QUIT:
            return minutes >= APP_QUIT_MIN_MINUTES
        return True

    def _queue(self, topic: str, reason: str, at: datetime) -> bool:
        denial = self.denial_reason(topic, at)
        self._log_decision(topic, at, denial)
        if denial is not None:
            return False
        self._pending.append(Trigger(topic=topic, reason=reason))
        return True

    def _log_decision(self, topic: str, at: datetime, denial: str | None) -> None:
        if denial is None:
            self._last_denial_log = None
            logger.info("主动判定：%s 允许发送（%s）", topic, at.isoformat())
            return
        key = (topic, denial)
        if key == self._last_denial_log:
            return
        self._last_denial_log = key
        logger.info("主动判定：%s 抑制，原因=%s（%s）", topic, denial, at.isoformat())
