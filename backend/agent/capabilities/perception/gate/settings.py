"""感知配置段：字段解码、默认值、范围校验与快照投射。

配置真源是领域状态（本类持有的 `PerceptionConfig`）；`data/settings.json` 的
`perception` 段只作持久化与初值来源（`D12`）。写入只改内存状态，落盘由
`app/settings/coordinator` 统一负责，本类不接触文件与数据库（`R8`）。
"""

import json
from dataclasses import dataclass, replace

from agent.capabilities.perception.constants import (
    DEFAULT_FOREGROUND_ENABLED,
    DEFAULT_IDLE_AWAY_MINUTES,
    DEFAULT_PERCEPTION_ENABLED,
    DEFAULT_SIT_MINUTES,
    MAX_AWAY_MINUTES,
    MAX_SIT_MINUTES,
    MIN_AWAY_MINUTES,
    MIN_SIT_MINUTES,
)
from agent.capabilities.perception.events.models import InvalidPerceptionConfig
from agent.spec.settings import SectionValues, SettingsSource

SECTION = "perception"


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


def decode(values: SectionValues) -> PerceptionConfig:
    """段字段 → 配置；缺字段、类型不符与非法取值一律回落默认值。"""
    return PerceptionConfig(
        enabled=_bool(values.get("enabled"), DEFAULT_PERCEPTION_ENABLED),
        foreground_enabled=_bool(values.get("foreground_enabled"), DEFAULT_FOREGROUND_ENABLED),
        idle_away_minutes=_minutes(values.get("idle_away_minutes"), DEFAULT_IDLE_AWAY_MINUTES),
        sit_minutes=_minutes(values.get("sit_minutes"), DEFAULT_SIT_MINUTES),
        whitelist=_names(values.get("process_whitelist")),
        muted=_names(values.get("process_muted")),
    )


def encode(config: PerceptionConfig) -> dict[str, object]:
    """配置 → 段字段；快照只存关键配置，派生值不入档。"""
    return {
        "enabled": config.enabled,
        "foreground_enabled": config.foreground_enabled,
        "idle_away_minutes": config.idle_away_minutes,
        "sit_minutes": config.sit_minutes,
        "process_whitelist": list(config.whitelist),
        "process_muted": list(config.muted),
    }


class PerceptionSettings:
    """领域状态持有者：首次读时从段快照起算，写入后以内存状态为准。"""

    def __init__(self, source: SettingsSource) -> None:
        self._source = source
        self._state: PerceptionConfig | None = None

    def read(self) -> PerceptionConfig:
        if self._state is None:
            self._state = decode(self._source.section(SECTION))
        return self._state

    def write(self, config: PerceptionConfig) -> None:
        validate_config(config)
        self._state = config

    def write_names(
        self,
        *,
        whitelist: tuple[str, ...] | None = None,
        muted: tuple[str, ...] | None = None,
    ) -> None:
        current = self.read()
        self._state = replace(
            current,
            whitelist=current.whitelist if whitelist is None else whitelist,
            muted=current.muted if muted is None else muted,
        )

    def snapshot(self) -> dict[str, object]:
        return encode(self.read())


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off"}
    return default


def _minutes(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return default
        return parsed if parsed > 0 else default
    return default


def _names(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip().lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if name and name not in names:
            names.append(name)
    return tuple(names)
