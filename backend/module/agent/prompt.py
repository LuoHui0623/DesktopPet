from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from string import Template
from typing import Literal

from config.persona import THEME_SPECS, FieldsSpec, FieldsTheme, ListSpec, ListTheme, PersonaConfig
from constants.files import RESOURCE_ORDER, RESOURCES
from constants.path import CAPABILITIES_FILE
from constants.speech import PERSONA_HINT_CHARS, SENTENCE_MAX_CHARS, SENTENCE_MAX_WIDTH
from module.agent.chat_system_prompt import (
    CATEGORY_ORDER,
    CHAT_PLACEHOLDERS,
    SEPARATOR_WIDTH,
    TOPIC_BODIES,
    TOPIC_REGISTRY,
    Topic,
    TopicContext,
)

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
    topics: tuple[Topic, ...]
    digest: str


@dataclass(frozen=True)
class SystemPrompt:
    text: str
    template_digest: str


# 占位值保持为 `$名称`，这样拼出的骨架仍可用 Template 校验占位符集合，
# 也能让 digest 只反映主题正文与范畴布局，而不随人设或能力清单变化。
TEMPLATE_CONTEXT = TopicContext(
    persona_section="$persona_section",
    capabilities="$capabilities",
    resources="$resources",
    sentence_max_chars="$sentence_max_chars",
    sentence_max_width="$sentence_max_width",
    persona_hint_chars="$persona_hint_chars",
)


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


def category_marker(category: str) -> str:
    """范畴分隔线：先格式化成范畴名再居中，宽度与日期线、current 线一致。"""
    return category.center(SEPARATOR_WIDTH, "=")


def _validate_topics(topics: Sequence[Topic]) -> set[str]:
    seen: set[str] = set()
    for topic in topics:
        if not topic.id.strip():
            raise PromptError("系统提示词主题 ID 不能为空")
        if topic.id in seen:
            raise PromptError(f"系统提示词主题 ID 重复：{topic.id}")
        if topic.category not in CATEGORY_ORDER:
            raise PromptError(f"系统提示词主题 {topic.id} 的范畴不合法：{topic.category}")
        seen.add(topic.id)
    return seen


def _validate_bodies(topics: Sequence[Topic]) -> None:
    registered = _validate_topics(topics)
    if set(TOPIC_BODIES) != registered:
        raise PromptError(
            f"系统提示词正文覆盖的主题应为 {sorted(registered)}，实际为 {sorted(TOPIC_BODIES)}"
        )
    found: set[str] = set()
    for topic in topics:
        try:
            found.update(Template(TOPIC_BODIES[topic.id]).get_identifiers())
        except ValueError as exc:
            raise PromptError(f"系统提示词主题 {topic.id} 的占位符不合法：{exc}") from exc
    if found != CHAT_PLACEHOLDERS:
        raise PromptError(
            f"系统提示词的占位符应为 {sorted(CHAT_PLACEHOLDERS)}，实际为 {sorted(found)}"
        )


def _grouped_topics(topics: Sequence[Topic]) -> list[tuple[str, list[Topic]]]:
    """按固定范畴顺序分组；组内沿用注册表的相对顺序。"""
    groups: list[tuple[str, list[Topic]]] = []
    for category in CATEGORY_ORDER:
        members = [topic for topic in topics if topic.category == category]
        if members:
            groups.append((category, members))
    return groups


def compose_chat_prompt(
    context: TopicContext,
    topics: Sequence[Topic] = TOPIC_REGISTRY,
) -> str:
    """按范畴顺序装配系统提示词：范畴线、主题标题、主题正文，段间空一行。"""
    _validate_topics(topics)
    blocks: list[str] = []
    for category, members in _grouped_topics(topics):
        blocks.append(category_marker(category))
        for topic in members:
            try:
                body = topic.render(context)
            except (KeyError, ValueError) as exc:
                raise PromptError(f"系统提示词主题 {topic.id} 渲染失败：{exc}") from exc
            if not body.strip():
                raise PromptError(f"系统提示词正文为空：{topic.id}")
            blocks.append(f"【{topic.id}】\n{body}")
    return "\n\n".join(blocks)


def load_templates() -> PromptTemplates:
    _validate_bodies(TOPIC_REGISTRY)
    text = compose_chat_prompt(TEMPLATE_CONTEXT, TOPIC_REGISTRY)
    return PromptTemplates(
        topics=TOPIC_REGISTRY,
        digest=sha256(text.encode("utf-8")).hexdigest(),
    )


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
    context = TopicContext(
        persona_section=render_persona(persona),
        capabilities=_render_capabilities(capabilities),
        resources=_render_resources(resources),
        sentence_max_chars=str(SENTENCE_MAX_CHARS),
        sentence_max_width=str(SENTENCE_MAX_WIDTH),
        persona_hint_chars=str(PERSONA_HINT_CHARS),
    )
    text = compose_chat_prompt(context, templates.topics)
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
