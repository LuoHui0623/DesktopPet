import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from config.persona import PersonaConfig
from constants.memory import (
    CONTEXT_TOKEN_BUDGET,
    FRAGMENT_TOKEN_BUDGET,
    STRUCTURED_MEMORY_TOKEN_BUDGET,
)
from service.agent.prompt import CapabilityDescription, PromptTemplates, build_system_prompt
from service.memory.daily_events import event_in_context, event_text
from service.memory.daily_log_service import DailyLogService
from service.memory.memory_service import MemoryReadError, StructuredMemoryDoc

KIND_STRUCTURED = "结构化记忆"
KIND_DAILY = "每日对话"
CITATION_RULE = (
    "回复引用上面【检索到的记忆】中的内容时，在该句末尾附加 [引用 日期]，"
    "日期取自对应条目的日期。只引用上面列出的内容，不要编造日期。"
)


def _output_example(text: str) -> str:
    # 历史助手消息也按结构化输出格式呈现，避免模型模仿纯文本回复
    return json.dumps({"type": "chat", "text": text}, ensure_ascii=False)


@dataclass(frozen=True)
class MemoryFragment:
    kind: str
    date: str
    text: str


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


def _truncate_lines(lines: Sequence[str], budget: int) -> str:
    kept: list[str] = []
    used = 0
    for line in lines:
        size = len(line) + 1
        if used + size > budget:
            break
        kept.append(line)
        used += size
    if not kept:
        return ""
    return "\n".join([*kept, "……（已截断）"])


def render_fragment(fragment: MemoryFragment) -> str:
    tags = (fragment.kind, fragment.date)
    return f"[{' · '.join(tags)}] {_flatten(fragment.text)}"


class ContextBuilder:
    def __init__(
        self,
        persona: PersonaConfig,
        templates: PromptTemplates,
        daily: DailyLogService,
        timezone: tzinfo,
        *,
        structured: StructuredMemoryDoc | None = None,
        clock: Callable[[tzinfo], datetime] | None = None,
        capabilities: Sequence[CapabilityDescription] = (),
    ) -> None:
        self._persona = persona
        self._templates = templates
        self._daily = daily
        self._timezone = timezone
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

        structured_block = self._structured_block(warnings)
        system_text = prompt.text
        if structured_block:
            system_text += f"\n\n【结构化记忆】\n{structured_block}"

        kept, dropped = self._fit_fragments(fragments)
        if dropped:
            warnings.append(f"检索到的记忆超出预算，已舍弃 {dropped} 条")
        if kept:
            fragment_block = "\n".join(render_fragment(fragment) for fragment in kept)
            system_text += f"\n\n【检索到的记忆】\n{fragment_block}"

        injected = tuple(kept)
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

    def _structured_block(self, warnings: list[str]) -> str:
        if self._structured is None:
            return ""
        try:
            lines = self._structured.lines()
        except (MemoryReadError, OSError):
            warnings.append("结构化记忆无法读取，已跳过")
            return ""
        text = "\n".join(lines).strip()
        if not text:
            return ""
        if len(text) > STRUCTURED_MEMORY_TOKEN_BUDGET:
            warnings.append(f"结构化记忆超出预算：{len(text)} 字符，已截断")
            return _truncate_lines(lines, STRUCTURED_MEMORY_TOKEN_BUDGET)
        return text

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
                turns[-1].append(AIMessage(content=_output_example(record.content)))
            elif record.role == "event" and event_in_context(record.content):
                note = SystemMessage(content=f"[事件] {event_text(record.content)}")
                if turns:
                    turns[-1].append(note)
                else:
                    turns.append([note])
        return turns
