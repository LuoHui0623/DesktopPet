from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Literal

from config.persona import THEME_SPECS, FieldsSpec, FieldsTheme, ListSpec, ListTheme, PersonaConfig
from constants.files import RESOURCE_ORDER, RESOURCES
from constants.path import CAPABILITIES_FILE
from constants.speech import PERSONA_HINT_CHARS, SENTENCE_MAX_CHARS, SENTENCE_MAX_WIDTH
from module.agent.chat_system_prompt import CHAT_PLACEHOLDERS, TOPIC_BODIES, TOPIC_ORDER

AgentRole = Literal["chat"]

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
    resource_id: str


@dataclass(frozen=True)
class PromptTemplates:
    chat: Template
    digest: str


@dataclass(frozen=True)
class SystemPrompt:
    text: str
    template_digest: str


def load_capabilities(path: Path = CAPABILITIES_FILE) -> tuple[CapabilityDescription, ...]:
    if not path.exists():
        return ()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"无法读取能力列表：{exc}") from exc
    capabilities: list[CapabilityDescription] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("- "):
            continue
        name, separator, description = line[2:].partition("：")
        if not separator or not name.strip() or not description.strip():
            raise PromptError(f"能力列表第 {number} 行格式不正确，应为“- 名称：描述”")
        capabilities.append(CapabilityDescription(name.strip(), description.strip()))
    return tuple(capabilities)


def merge_capabilities(
    *groups: Sequence[CapabilityDescription],
) -> tuple[CapabilityDescription, ...]:
    """按名称合并多组能力描述，先出现的优先，用于文件配置与注册表叠加。"""
    merged: dict[str, CapabilityDescription] = {}
    for group in groups:
        for item in group:
            merged.setdefault(item.name, item)
    return tuple(merged.values())


def default_resources() -> tuple[ResourceItem, ...]:
    return tuple(ResourceItem(RESOURCES[key].label, key) for key in RESOURCE_ORDER)


def compose_chat_prompt(
    order: Sequence[str] | None = None,
    bodies: Mapping[str, str] | None = None,
) -> str:
    """把有序主题拼成系统提示词：每段包成「【主题】\\n正文」，段间空一行。"""
    topics = TOPIC_ORDER if order is None else order
    contents = TOPIC_BODIES if bodies is None else bodies
    blocks: list[str] = []
    for topic in topics:
        body = contents.get(topic)
        if body is None:
            raise PromptError(f"系统提示词缺少正文：{topic}")
        if not body.strip():
            raise PromptError(f"系统提示词正文为空：{topic}")
        blocks.append(f"【{topic}】\n{body}")
    text = "\n\n".join(blocks)
    try:
        found = frozenset(Template(text).get_identifiers())
    except ValueError as exc:
        raise PromptError(f"系统提示词占位符不合法：{exc}") from exc
    if found != CHAT_PLACEHOLDERS:
        raise PromptError(
            f"系统提示词的占位符应为 {sorted(CHAT_PLACEHOLDERS)}，实际为 {sorted(found)}"
        )
    return text


def load_templates() -> PromptTemplates:
    text = compose_chat_prompt(order=TOPIC_ORDER, bodies=TOPIC_BODIES)
    return PromptTemplates(chat=Template(text), digest=sha256(text.encode("utf-8")).hexdigest())


def read_template(directory: Path, name: str, placeholders: frozenset[str]) -> str:
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


def _render_fields(spec: FieldsSpec, theme: FieldsTheme) -> str:
    head = spec.template.format(**{key: theme.values[key] for key in spec.required})
    extras = [
        f"{key}：{theme.values[key]}" for key in sorted(theme.values) if key not in spec.required
    ]
    return "\n".join([head, *extras])


def _render_list(spec: ListSpec, theme: ListTheme) -> str:
    return "\n".join([f"{spec.heading}：", *(f"- {item}" for item in theme.items)])


def render_persona(persona: PersonaConfig) -> str:
    blocks: list[str] = []
    for key, spec in THEME_SPECS.items():
        theme = persona.themes.get(key)
        if theme is None:
            continue
        if isinstance(spec, FieldsSpec) and isinstance(theme, FieldsTheme):
            blocks.append(_render_fields(spec, theme))
        elif isinstance(spec, ListSpec) and isinstance(theme, ListTheme):
            blocks.append(_render_list(spec, theme))
        else:
            raise PromptError(f"主题 {key} 的解析方式与渲染方式不一致")
    return "\n\n".join(blocks)


def _render_capabilities(capabilities: Sequence[CapabilityDescription]) -> str:
    if not capabilities:
        return NO_CAPABILITIES
    return "\n".join(
        f"- {item.name}: {item.description}" if item.description else f"- {item.name}"
        for item in capabilities
    )


def _render_resources(resources: Sequence[ResourceItem]) -> str:
    if not resources:
        return NO_RESOURCES
    return "\n".join(f"- {item.label}: {item.resource_id}" for item in resources)


def _build_chat(
    persona: PersonaConfig,
    templates: PromptTemplates,
    capabilities: Sequence[CapabilityDescription],
    resources: Sequence[ResourceItem],
) -> SystemPrompt:
    persona_section = render_persona(persona)
    text = templates.chat.substitute(
        persona_section=persona_section,
        capabilities=_render_capabilities(capabilities),
        resources=_render_resources(resources),
        sentence_max_chars=str(SENTENCE_MAX_CHARS),
        sentence_max_width=str(SENTENCE_MAX_WIDTH),
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
