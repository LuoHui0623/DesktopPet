"""设置快照的段定义：段名、默认值与段级归一化。

字段语义、默认值与合法区间由各域拥有——感知段在 `agent/capabilities/perception/
gate/settings.py`、调度段在同目录 `scheduler.py`、互动段在 `interaction/config.py`、
人设段在 `agent/persona/persona_service.py`、模型段在 `agent/llm/active.py`、
形象段在 `app/avatar/avatar_service.py`。本模块只做聚合，避免同一份默认值在两处
定义后漂移（`R7`）。

归一化 = 解码再编码：缺字段补默认、非法取值回落默认、段内未知字段丢弃。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agent.capabilities.perception.gate import scheduler as scheduler_settings
from agent.capabilities.perception.gate import settings as perception_settings
from agent.capabilities.perception.interaction import config as interaction_config
from agent.llm import active as llm_active
from agent.persona import persona_service
from app.avatar import avatar_service

SECTION_PERCEPTION = "perception"
SECTION_SCHEDULER = "scheduler"
SECTION_AVATAR = "avatar"
SECTION_PERSONA = "persona"
SECTION_LLM = "llm"
SECTION_INTERACTION = "interaction"

SECTIONS: tuple[str, ...] = (
    SECTION_PERCEPTION,
    SECTION_SCHEDULER,
    SECTION_AVATAR,
    SECTION_PERSONA,
    SECTION_LLM,
    SECTION_INTERACTION,
)


@dataclass(frozen=True)
class SectionSpec:
    name: str
    normalize: Callable[[object], dict[str, object]]


def _spec(
    name: str,
    decode: Callable[[Mapping[str, object]], object],
    encode: Callable[[object], dict[str, object]],
) -> SectionSpec:
    def normalize(values: object) -> dict[str, object]:
        return encode(decode(values if isinstance(values, Mapping) else {}))

    return SectionSpec(name=name, normalize=normalize)


SPECS: dict[str, SectionSpec] = {
    spec.name: spec
    for spec in (
        _spec(SECTION_PERCEPTION, perception_settings.decode, perception_settings.encode),
        _spec(SECTION_SCHEDULER, scheduler_settings.decode, scheduler_settings.encode),
        _spec(SECTION_AVATAR, avatar_service.decode_active, avatar_service.encode_active),
        _spec(SECTION_PERSONA, persona_service.decode, persona_service.encode),
        _spec(SECTION_LLM, llm_active.decode, llm_active.encode),
        _spec(SECTION_INTERACTION, interaction_config.decode, interaction_config.encode),
    )
}


def normalize(name: str, values: object) -> dict[str, object]:
    """把一个段的任意输入归一化为合法字段集；未知段按 KeyError 处理。"""
    return SPECS[name].normalize(values)


def is_section(name: str) -> bool:
    return name in SPECS


def defaults() -> dict[str, dict[str, object]]:
    """六段默认值；默认值本身来自各域解码器，不在此另立一份。"""
    return {name: SPECS[name].normalize({}) for name in SECTIONS}
