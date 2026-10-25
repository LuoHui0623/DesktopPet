from collections.abc import Sequence
from dataclasses import dataclass
from string import Template
from typing import Any, Literal

from config.persona import PersonaConfig
from constants.path import PROMPTS_DIR

AgentRole = Literal["chat"]

PERSONA_TEMPLATE = "persona.md"
PERSONA_PLACEHOLDERS = frozenset({"persona"})
CHAT_TEMPLATE = "chat.md"
CHAT_PLACEHOLDERS = frozenset({"persona_section", "capabilities"})
NO_CAPABILITIES = "暂无可用能力。"


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class CapabilityDescription:
    name: str
    description: str


def _load_template(name: str, placeholders: frozenset[str]) -> Template:
    path = PROMPTS_DIR / name
    try:
        template = Template(path.read_text(encoding="utf-8"))
        found = frozenset(template.get_identifiers())
    except (OSError, ValueError) as exc:
        raise PromptError(f"无法读取提示词模板 {name}：{exc}") from exc
    if found != placeholders:
        raise PromptError(
            f"提示词模板 {name} 的占位符应为 {sorted(placeholders)}，实际为 {sorted(found)}"
        )
    return template


def _render_value(key: str, value: Any, indent: int) -> list[str]:
    pad = "  " * indent
    if isinstance(value, str):
        return [f"{pad}{key}: {value}"]
    if isinstance(value, tuple):
        return [f"{pad}{key}:", *(f"{pad}  - {item}" for item in value)]
    lines = [f"{pad}{key}:"]
    for child_key, child in value.items():
        lines.extend(_render_value(child_key, child, indent + 1))
    return lines


def render_persona(persona: PersonaConfig) -> str:
    lines: list[str] = []
    for key, value in persona.content.items():
        lines.extend(_render_value(key, value, 0))
    return "\n".join(lines)


def _render_capabilities(capabilities: Sequence[CapabilityDescription]) -> str:
    if not capabilities:
        return NO_CAPABILITIES
    return "\n".join(f"- {item.name}: {item.description}" for item in capabilities)


def _build_chat(persona: PersonaConfig, capabilities: Sequence[CapabilityDescription]) -> str:
    persona_template = _load_template(PERSONA_TEMPLATE, PERSONA_PLACEHOLDERS)
    chat_template = _load_template(CHAT_TEMPLATE, CHAT_PLACEHOLDERS)
    persona_section = persona_template.substitute(persona=render_persona(persona))
    return chat_template.substitute(
        persona_section=persona_section,
        capabilities=_render_capabilities(capabilities),
    )


def build_system_prompt(
    role: AgentRole,
    persona: PersonaConfig,
    capabilities: Sequence[CapabilityDescription] = (),
) -> str:
    match role:
        case "chat":
            return _build_chat(persona, capabilities)
    raise PromptError(f"未知的 agent 角色：{role}")
