from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Any, Literal

from config.persona import PersonaConfig
from constants.path import DRAFT_PATH, EMOJI_DIR, PROMPTS_DIR, ROOT_DIR
from constants.speech import PERSONA_HINT_CHARS, SENTENCE_MAX_CHARS

AgentRole = Literal["chat"]

PERSONA_TEMPLATE = "persona.md"
PERSONA_PLACEHOLDERS = frozenset({"persona"})
CHAT_TEMPLATE = "chat.md"
CHAT_PLACEHOLDERS = frozenset(
    {
        "persona_section",
        "capabilities",
        "resources",
        "sentence_max_chars",
        "persona_hint_chars",
    }
)
NO_CAPABILITIES = "暂无可用能力。"
NO_RESOURCES = "暂无可用资源。"


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class CapabilityDescription:
    name: str
    description: str


@dataclass(frozen=True)
class ResourceItem:
    label: str
    path: str


@dataclass(frozen=True)
class PromptTemplates:
    persona: Template
    chat: Template
    digest: str


@dataclass(frozen=True)
class SystemPrompt:
    text: str
    template_digest: str


def default_resources() -> tuple[ResourceItem, ...]:
    return (
        ResourceItem("草稿本", _relative(DRAFT_PATH)),
        ResourceItem("表情包", _relative(EMOJI_DIR)),
    )


def _relative(path: Path) -> str:
    return path.relative_to(ROOT_DIR).as_posix()


def load_templates(directory: Path = PROMPTS_DIR) -> PromptTemplates:
    persona_text = _read(directory, PERSONA_TEMPLATE, PERSONA_PLACEHOLDERS)
    chat_text = _read(directory, CHAT_TEMPLATE, CHAT_PLACEHOLDERS)
    digest = sha256(
        "\n".join([PERSONA_TEMPLATE, persona_text, CHAT_TEMPLATE, chat_text]).encode("utf-8")
    ).hexdigest()
    return PromptTemplates(
        persona=Template(persona_text),
        chat=Template(chat_text),
        digest=digest,
    )


def _read(directory: Path, name: str, placeholders: frozenset[str]) -> str:
    path = directory / name
    try:
        text = path.read_text(encoding="utf-8")
        found = frozenset(Template(text).get_identifiers())
    except (OSError, ValueError) as exc:
        raise PromptError(f"无法读取提示词模板 {name}：{exc}") from exc
    if found != placeholders:
        raise PromptError(
            f"提示词模板 {name} 的占位符应为 {sorted(placeholders)}，实际为 {sorted(found)}"
        )
    return text


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


def _render_resources(resources: Sequence[ResourceItem]) -> str:
    if not resources:
        return NO_RESOURCES
    return "\n".join(f"- {item.label}: {item.path}" for item in resources)


def _build_chat(
    persona: PersonaConfig,
    templates: PromptTemplates,
    capabilities: Sequence[CapabilityDescription],
    resources: Sequence[ResourceItem],
) -> SystemPrompt:
    persona_section = templates.persona.substitute(persona=render_persona(persona))
    text = templates.chat.substitute(
        persona_section=persona_section,
        capabilities=_render_capabilities(capabilities),
        resources=_render_resources(resources),
        sentence_max_chars=str(SENTENCE_MAX_CHARS),
        persona_hint_chars=str(PERSONA_HINT_CHARS),
    )
    return SystemPrompt(text=text, template_digest=templates.digest)


def build_system_prompt(
    role: AgentRole,
    persona: PersonaConfig,
    templates: PromptTemplates,
    capabilities: Sequence[CapabilityDescription] = (),
    resources: Sequence[ResourceItem] | None = None,
) -> SystemPrompt:
    if resources is None:
        resources = default_resources()
    match role:
        case "chat":
            return _build_chat(persona, templates, capabilities, resources)
    raise PromptError(f"未知的 agent 角色：{role}")
