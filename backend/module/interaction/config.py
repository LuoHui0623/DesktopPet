import json
from dataclasses import asdict, replace

from module.interaction.contracts import (
    INTERACTION_KINDS,
    InteractionConfig,
    InteractionKind,
)

INTERACTION_CONFIG_KEY = "interaction.config"

DEFAULT_INTERACTION_CONFIG = InteractionConfig(
    report_enabled_kinds=("click", "hover", "drag", "menu"),
    hover_dwell_ms=300,
    report_interval_ms=1000,
    reaction_ms=220,
)


def _bounded(value: object, low: int, high: int, current: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return current
    return value if low <= value <= high else current


def merge_interaction_config(base: InteractionConfig, patch: object) -> InteractionConfig:
    """把设置页提交的局部更新合并到当前配置；非法取值保持原值。"""
    config = base
    if not isinstance(patch, dict):
        return config
    kinds = patch.get("report_enabled_kinds")
    if isinstance(kinds, list):
        selected = {item for item in kinds if isinstance(item, str)}
        known = tuple(kind for kind in INTERACTION_KINDS if kind in selected)
        # 空列表是“全部不上报”的合法选择；只含未知类型的列表按非法取值处理，保持原值
        if known or not kinds:
            config = replace(config, report_enabled_kinds=known)
    config = replace(
        config,
        hover_dwell_ms=_bounded(patch.get("hover_dwell_ms"), 100, 3000, config.hover_dwell_ms),
        report_interval_ms=_bounded(
            patch.get("report_interval_ms"), 0, 10_000, config.report_interval_ms
        ),
        reaction_ms=_bounded(patch.get("reaction_ms"), 0, 3000, config.reaction_ms),
    )
    return config


def is_kind_enabled(config: InteractionConfig, kind: InteractionKind) -> bool:
    return kind in config.report_enabled_kinds


def encode_interaction_config(config: InteractionConfig) -> str:
    return json.dumps(asdict(config), ensure_ascii=False, sort_keys=True)


def decode_interaction_config(raw: str | None) -> InteractionConfig:
    if not raw:
        return DEFAULT_INTERACTION_CONFIG
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return DEFAULT_INTERACTION_CONFIG
    return merge_interaction_config(DEFAULT_INTERACTION_CONFIG, data)
