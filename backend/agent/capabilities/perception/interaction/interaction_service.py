import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from agent.capabilities.perception.interaction.config import (
    SECTION,
    decode,
    encode,
    is_kind_enabled,
    merge_interaction_config,
)
from agent.capabilities.perception.interaction.constants import (
    PENDING_LIMIT,
    THROTTLE_SECONDS,
    TTL_SECONDS,
)
from agent.capabilities.perception.interaction.contracts import (
    Interaction,
    InteractionConfig,
    InteractionKind,
    InteractionOutcome,
)
from agent.spec.settings import SettingsSource


class InteractionService:
    """只保留“下一次对话要带上”的互动，不落库；配置真源是 `interaction` 段。"""

    def __init__(
        self,
        settings: SettingsSource | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._config: InteractionConfig | None = None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._pending: deque[Interaction] = deque()
        self._last: dict[str, datetime] = {}

    def record(self, kind: InteractionKind) -> InteractionOutcome:
        now = self._clock()
        if not is_kind_enabled(self.config(), kind):
            return InteractionOutcome(False, "disabled")
        with self._lock:
            last = self._last.get(kind)
            if last is not None and now - last < timedelta(seconds=THROTTLE_SECONDS):
                return InteractionOutcome(False, "throttled")
            self._last[kind] = now
            self._pending.append(Interaction(kind=kind, at=now))
            while len(self._pending) > PENDING_LIMIT:
                self._pending.popleft()
        return InteractionOutcome(True)

    def config(self) -> InteractionConfig:
        if self._config is None:
            values = {} if self._settings is None else self._settings.section(SECTION)
            self._config = decode(values)
        return self._config

    def write_config(self, patch: object) -> InteractionConfig:
        """只改领域状态；落盘由设置协调器负责。"""
        self._config = merge_interaction_config(self.config(), patch)
        return self._config

    def snapshot_settings(self) -> dict[str, object]:
        return encode(self.config())

    def drain(self) -> tuple[Interaction, ...]:
        cutoff = self._clock() - timedelta(seconds=TTL_SECONDS)
        with self._lock:
            kept = tuple(item for item in self._pending if item.at >= cutoff)
            self._pending.clear()
        return kept
