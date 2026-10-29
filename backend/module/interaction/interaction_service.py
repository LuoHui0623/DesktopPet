import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from module.interaction.config import (
    DEFAULT_INTERACTION_CONFIG,
    INTERACTION_CONFIG_KEY,
    decode_interaction_config,
    encode_interaction_config,
    is_kind_enabled,
    merge_interaction_config,
)
from module.interaction.constants import PENDING_LIMIT, THROTTLE_SECONDS, TTL_SECONDS
from module.interaction.contracts import (
    Interaction,
    InteractionConfig,
    InteractionKind,
    InteractionOutcome,
)
from module.settings.settings_service import SettingsService


class InteractionService:
    """只保留“下一次对话要带上”的互动，不落库；配置真源是 app_setting。"""

    def __init__(
        self,
        settings: SettingsService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
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
        if self._settings is None:
            return DEFAULT_INTERACTION_CONFIG
        return decode_interaction_config(self._settings.get(INTERACTION_CONFIG_KEY))

    def write_config(self, patch: object) -> InteractionConfig:
        merged = merge_interaction_config(self.config(), patch)
        if self._settings is not None:
            self._settings.put(INTERACTION_CONFIG_KEY, encode_interaction_config(merged))
        return merged

    def drain(self) -> tuple[Interaction, ...]:
        cutoff = self._clock() - timedelta(seconds=TTL_SECONDS)
        with self._lock:
            kept = tuple(item for item in self._pending if item.at >= cutoff)
            self._pending.clear()
        return kept
