import json
from dataclasses import dataclass

from module.settings.settings_service import SettingsService
from service.perception.constants import (
    DEFAULT_FOREGROUND_ENABLED,
    DEFAULT_IDLE_AWAY_MINUTES,
    DEFAULT_PERCEPTION_ENABLED,
    DEFAULT_SIT_MINUTES,
    FOREGROUND_ENABLED_KEY,
    IDLE_AWAY_MINUTES_KEY,
    MAX_AWAY_MINUTES,
    MAX_SIT_MINUTES,
    MIN_AWAY_MINUTES,
    MIN_SIT_MINUTES,
    PERCEPTION_ENABLED_KEY,
    PROCESS_MUTED_KEY,
    PROCESS_WHITELIST_KEY,
    SIT_MINUTES_KEY,
)
from service.perception.models import InvalidPerceptionConfig


@dataclass(frozen=True)
class PerceptionConfig:
    enabled: bool
    foreground_enabled: bool
    idle_away_minutes: int
    sit_minutes: int
    whitelist: tuple[str, ...]
    muted: tuple[str, ...]


def validate_config(config: PerceptionConfig) -> None:
    if not MIN_AWAY_MINUTES <= config.idle_away_minutes <= MAX_AWAY_MINUTES:
        raise InvalidPerceptionConfig(
            f"离开判定阈值必须在 {MIN_AWAY_MINUTES} 到 {MAX_AWAY_MINUTES} 分钟之间，"
            f"当前为 {config.idle_away_minutes}"
        )
    if not MIN_SIT_MINUTES <= config.sit_minutes <= MAX_SIT_MINUTES:
        raise InvalidPerceptionConfig(
            f"久坐判定阈值必须在 {MIN_SIT_MINUTES} 到 {MAX_SIT_MINUTES} 分钟之间，"
            f"当前为 {config.sit_minutes}"
        )


class PerceptionSettings:
    def __init__(self, settings: SettingsService) -> None:
        self._settings = settings

    def read(self) -> PerceptionConfig:
        return PerceptionConfig(
            enabled=self._bool(PERCEPTION_ENABLED_KEY, DEFAULT_PERCEPTION_ENABLED),
            foreground_enabled=self._bool(FOREGROUND_ENABLED_KEY, DEFAULT_FOREGROUND_ENABLED),
            idle_away_minutes=self._minutes(IDLE_AWAY_MINUTES_KEY, DEFAULT_IDLE_AWAY_MINUTES),
            sit_minutes=self._minutes(SIT_MINUTES_KEY, DEFAULT_SIT_MINUTES),
            whitelist=self._names(PROCESS_WHITELIST_KEY),
            muted=self._names(PROCESS_MUTED_KEY),
        )

    def write(self, config: PerceptionConfig) -> None:
        validate_config(config)
        self._settings.put(PERCEPTION_ENABLED_KEY, "true" if config.enabled else "false")
        self._settings.put(FOREGROUND_ENABLED_KEY, "true" if config.foreground_enabled else "false")
        self._settings.put(IDLE_AWAY_MINUTES_KEY, str(config.idle_away_minutes))
        self._settings.put(SIT_MINUTES_KEY, str(config.sit_minutes))

    def write_names(self, key: str, names: tuple[str, ...]) -> None:
        self._settings.put(key, json.dumps(list(names), ensure_ascii=False))

    def _bool(self, key: str, default: bool) -> bool:
        raw = self._settings.get(key)
        if raw is None:
            return default
        return raw.strip().lower() not in {"false", "0", "no", "off"}

    def _minutes(self, key: str, default: int) -> int:
        raw = self._settings.get(key)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value > 0 else default

    def _names(self, key: str) -> tuple[str, ...]:
        raw = self._settings.get(key)
        if not raw:
            return ()
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return ()
        if not isinstance(values, list):
            return ()
        names = []
        for value in values:
            if not isinstance(value, str):
                continue
            name = value.strip().lower()
            if name.endswith(".exe"):
                name = name[:-4]
            if name and name not in names:
                names.append(name)
        return tuple(names)
