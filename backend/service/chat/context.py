from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from config.persona import PersonaConfig
from constants.memory import (
    CONTEXT_TOKEN_BUDGET,
    CORE_MEMORY_TOKEN_BUDGET,
    FRAGMENT_TOKEN_BUDGET,
)
from service.agent.prompt import CapabilityDescription, PromptTemplates, build_system_prompt
from service.memory.core_memory import CoreMemoryService
from service.memory.daily_events import event_in_context, event_text
from service.memory.daily_log_service import DailyLogService
from service.memory.memory_service import MemoryServiceError, StructuredMemoryService

KIND_STRUCTURED = "结构化记忆"
KIND_DAILY = "每日对话"
CATEGORY_LABELS = {
    "profile": "基础信息",
    "preference": "偏好",
    "habit": "习惯",
    "event": "事项",
    "interaction": "互动约定",
}
CITATION_RULE = (
    "回复引用上面【核心典藏】或【检索到的记忆】中的内容时，在该句末尾附加 [引用 ID]。"
    "ID 取自对应条目的“id”；每日对话条目没有 id，取其日期。"
    "只引用上面列出的内容，不要编造 ID。"
)


@dataclass(frozen=True)
class MemoryFragment:
    kind: str
    date: str
    text: str
    category: str | None = None
    memory_id: str | None = None


@dataclass(frozen=True)
class AssembledContext:
    messages: list[BaseMessage]
    template_digest: str
    chars: int
    trimmed_turns: int
    dropped_fragments: int
    warnings: tuple[str, ...]
    injected: tuple[MemoryFragment, ...] = ()


def _flatten(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", " ")


def render_fragment(fragment: MemoryFragment) -> str:
    tags = [fragment.kind]
    if fragment.category is not None:
        tags.append(CATEGORY_LABELS.get(fragment.category, fragment.category))
    tags.append(fragment.date)
    line = f"[{' · '.join(tags)}] {_flatten(fragment.text)}"
    if fragment.kind == KIND_STRUCTURED and fragment.memory_id:
        line += f"（id：{fragment.memory_id}）"
    return line


class ContextBuilder:
    def __init__(
        self,
        persona: PersonaConfig,
        templates: PromptTemplates,
        daily: DailyLogService,
        timezone: tzinfo,
        *,
        core: CoreMemoryService | None = None,
        structured: StructuredMemoryService | None = None,
        clock: Callable[[tzinfo], datetime] | None = None,
        capabilities: Sequence[CapabilityDescription] = (),
    ) -> None:
        self._persona = persona
        self._templates = templates
        self._daily = daily
        self._timezone = timezone
        self._core = core
        self._structured = structured
        self._clock = clock or (lambda zone: datetime.now(zone))
        self._capabilities = tuple(capabilities)

    def build(
        self,
        user_text: str,
        fragments: Sequence[MemoryFragment] = (),
    ) -> AssembledContext:
        prompt = build_system_prompt(
            "chat", self._persona, self._templates, capabilities=self._capabilities
        )
        warnings: list[str] = []

        core_fragments = self._core_fragments(warnings)
        core_block = "\n".join(render_fragment(fragment) for fragment in core_fragments)
        if len(core_block) > CORE_MEMORY_TOKEN_BUDGET:
            warnings.append(f"核心典藏超出预算：{len(core_block)} 字符")
        system_text = prompt.text
        if core_fragments:
            system_text += f"\n\n【核心典藏】\n{core_block}"

        kept, dropped = self._fit_fragments(fragments)
        if dropped:
            warnings.append(f"检索到的记忆超出预算，已舍弃 {dropped} 条")
        if kept:
            fragment_block = "\n".join(render_fragment(fragment) for fragment in kept)
            system_text += f"\n\n【检索到的记忆】\n{fragment_block}"

        injected = (*core_fragments, *kept)
        if injected:
            system_text += f"\n\n{CITATION_RULE}"

        fixed = len(system_text) + len(user_text)
        turns = self._recent_turns()
        kept_turns: list[list[BaseMessage]] = []
        used = 0
        for turn in reversed(turns):
            size = sum(len(message.content) for message in turn)
            if fixed + used + size > CONTEXT_TOKEN_BUDGET:
                break
            kept_turns.insert(0, turn)
            used += size
        trimmed = len(turns) - len(kept_turns)
        if trimmed:
            warnings.append(f"近期对话超出预算，已裁剪最早的 {trimmed} 轮")
        if fixed > CONTEXT_TOKEN_BUDGET:
            warnings.append(f"固定部分超出预算：{fixed} 字符")

        messages: list[BaseMessage] = [
            SystemMessage(content=system_text),
            *[message for turn in kept_turns for message in turn],
            HumanMessage(content=user_text),
        ]
        return AssembledContext(
            messages=messages,
            template_digest=prompt.template_digest,
            chars=fixed + used,
            trimmed_turns=trimmed,
            dropped_fragments=dropped,
            warnings=tuple(warnings),
            injected=injected,
        )

    def _core_fragments(self, warnings: list[str]) -> list[MemoryFragment]:
        if self._core is None or self._structured is None:
            return []
        fragments: list[MemoryFragment] = []
        for memory_id in self._core.ids():
            try:
                memory = self._structured.get(memory_id)
            except MemoryServiceError:
                warnings.append(f"核心典藏 {memory_id} 无法读取，已跳过")
                continue
            fragments.append(
                MemoryFragment(
                    kind=KIND_STRUCTURED,
                    date=memory.source.date,
                    text=memory.content,
                    category=memory.category,
                    memory_id=memory.id,
                )
            )
        return fragments

    def _fit_fragments(
        self, fragments: Sequence[MemoryFragment]
    ) -> tuple[list[MemoryFragment], int]:
        kept: list[MemoryFragment] = []
        used = 0
        for index, fragment in enumerate(fragments):
            size = len(render_fragment(fragment))
            if used + size > FRAGMENT_TOKEN_BUDGET:
                return kept, len(fragments) - index
            kept.append(fragment)
            used += size
        return kept, 0

    def _recent_turns(self) -> list[list[BaseMessage]]:
        today = self._clock(self._timezone).date().isoformat()
        turns: list[list[BaseMessage]] = []
        for record in self._daily.read_day(today):
            if record.role == "master":
                turns.append([HumanMessage(content=record.content)])
            elif record.role == "me" and turns:
                turns[-1].append(AIMessage(content=record.content))
            elif record.role == "event" and event_in_context(record.content):
                note = SystemMessage(content=f"[事件] {event_text(record.content)}")
                if turns:
                    turns[-1].append(note)
                else:
                    turns.append([note])
        return turns
