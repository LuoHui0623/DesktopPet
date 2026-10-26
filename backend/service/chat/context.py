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
from service.agent.prompt import PromptTemplates, build_system_prompt
from service.memory.core_memory import CoreMemoryService
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


def _flatten(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", " ")


def render_fragment(fragment: MemoryFragment) -> str:
    tags = [fragment.kind]
    if fragment.category is not None:
        tags.append(CATEGORY_LABELS.get(fragment.category, fragment.category))
    tags.append(fragment.date)
    return f"[{' · '.join(tags)}] {_flatten(fragment.text)}"


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
    ) -> None:
        self._persona = persona
        self._templates = templates
        self._daily = daily
        self._timezone = timezone
        self._core = core
        self._structured = structured
        self._clock = clock or (lambda zone: datetime.now(zone))

    def build(
        self,
        user_text: str,
        fragments: Sequence[MemoryFragment] = (),
    ) -> AssembledContext:
        prompt = build_system_prompt("chat", self._persona, self._templates)
        warnings: list[str] = []

        core_lines = self._core_lines(warnings)
        core_block = "\n".join(core_lines)
        if len(core_block) > CORE_MEMORY_TOKEN_BUDGET:
            warnings.append(f"核心典藏超出预算：{len(core_block)} 字符")
        system_text = prompt.text
        if core_lines:
            system_text += f"\n\n【核心典藏】\n{core_block}"

        fragment_lines, dropped = self._fit_fragments(fragments)
        if dropped:
            warnings.append(f"检索到的记忆超出预算，已舍弃 {dropped} 条")
        if fragment_lines:
            system_text += "\n\n【检索到的记忆】\n" + "\n".join(fragment_lines)

        fixed = len(system_text) + len(user_text)
        turns = self._recent_turns()
        kept: list[list[BaseMessage]] = []
        used = 0
        for turn in reversed(turns):
            size = sum(len(message.content) for message in turn)
            if fixed + used + size > CONTEXT_TOKEN_BUDGET:
                break
            kept.insert(0, turn)
            used += size
        trimmed = len(turns) - len(kept)
        if trimmed:
            warnings.append(f"近期对话超出预算，已裁剪最早的 {trimmed} 轮")
        if fixed > CONTEXT_TOKEN_BUDGET:
            warnings.append(f"固定部分超出预算：{fixed} 字符")

        messages: list[BaseMessage] = [
            SystemMessage(content=system_text),
            *[message for turn in kept for message in turn],
            HumanMessage(content=user_text),
        ]
        return AssembledContext(
            messages=messages,
            template_digest=prompt.template_digest,
            chars=fixed + used,
            trimmed_turns=trimmed,
            dropped_fragments=dropped,
            warnings=tuple(warnings),
        )

    def _core_lines(self, warnings: list[str]) -> list[str]:
        if self._core is None or self._structured is None:
            return []
        lines: list[str] = []
        for memory_id in self._core.ids():
            try:
                memory = self._structured.get(memory_id)
            except MemoryServiceError:
                warnings.append(f"核心典藏 {memory_id} 无法读取，已跳过")
                continue
            lines.append(
                render_fragment(
                    MemoryFragment(
                        kind=KIND_STRUCTURED,
                        date=memory.source.date,
                        text=memory.content,
                        category=memory.category,
                        memory_id=memory.id,
                    )
                )
            )
        return lines

    def _fit_fragments(self, fragments: Sequence[MemoryFragment]) -> tuple[list[str], int]:
        lines: list[str] = []
        used = 0
        for index, fragment in enumerate(fragments):
            line = render_fragment(fragment)
            if used + len(line) > FRAGMENT_TOKEN_BUDGET:
                return lines, len(fragments) - index
            lines.append(line)
            used += len(line)
        return lines, 0

    def _recent_turns(self) -> list[list[BaseMessage]]:
        today = self._clock(self._timezone).date().isoformat()
        turns: list[list[BaseMessage]] = []
        for record in self._daily.read_day(today):
            if record.role == "user":
                turns.append([HumanMessage(content=record.content)])
            elif record.role == "assistant" and turns:
                turns[-1].append(AIMessage(content=record.content))
        return turns
