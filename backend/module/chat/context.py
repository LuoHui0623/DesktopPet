from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from config.persona import PersonaConfig
from constants.memory import FRAGMENT_TOKEN_BUDGET
from module.agent.prompt import (
    CapabilityDescription,
    PromptTemplates,
    ResourceItem,
    build_system_prompt,
)
from module.chat.transcript import ChatLine, TranscriptReader
from module.memory.memory_service import MemoryReadError, StructuredMemoryDoc
from service.perception.constants import CONTEXT_EVENT_LIMIT
from service.perception.events import PerceptionEvent

KIND_STRUCTURED = "结构化记忆"
KIND_DAILY = "每日对话"
CURRENT_SEPARATOR = "======= current ======="
DAY_MARKER = "======={day}======="
USER_ROLE = "master"
CITATION_RULE = (
    "回复引用上面【检索到的记忆】中的内容时，在该句末尾附加 [引用 日期]，"
    "日期取自对应条目的日期。只引用上面列出的内容，不要编造日期。"
)
PROACTIVE_TRIGGER = (
    "【本次触发】{reason}\n【当前时间】{time}\n现在主动说一句话。不要问问题，不要解释你在主动。"
)


class PerceptionReader(Protocol):
    def recent_context_events(
        self, limit: int = CONTEXT_EVENT_LIMIT
    ) -> Sequence[PerceptionEvent]: ...


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
    dropped_fragments: int
    warnings: tuple[str, ...]
    injected: tuple[MemoryFragment, ...] = ()
    notes: tuple[ChatLine, ...] = ()


def _flatten(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", " ")


def _stamp(clock: str, text: str, role: str = USER_ROLE) -> str:
    """给发言加角色与秒级时间前缀：`[role][HH:MM:SS]对话`。"""
    return f"[{role}][{clock}]{text}"


def render_fragment(fragment: MemoryFragment) -> str:
    tags = (fragment.kind, fragment.date)
    return f"[{' · '.join(tags)}] {_flatten(fragment.text)}"


class ContextBuilder:
    def __init__(
        self,
        persona: PersonaConfig,
        templates: PromptTemplates,
        timezone: tzinfo,
        *,
        structured: StructuredMemoryDoc | None = None,
        clock: Callable[[tzinfo], datetime] | None = None,
        capabilities: Sequence[CapabilityDescription] = (),
        resources: Sequence[ResourceItem] | None = None,
        perception: PerceptionReader | None = None,
        transcript: TranscriptReader | None = None,
    ) -> None:
        self._persona = persona
        self._templates = templates
        self._timezone = timezone
        self._structured = structured
        self._clock = clock or (lambda zone: datetime.now(zone))
        self._capabilities = tuple(capabilities)
        self._resources = resources
        self._perception = perception
        self._transcript = transcript

    def build(
        self,
        user_text: str | None = None,
        fragments: Sequence[MemoryFragment] = (),
        *,
        trigger_reason: str | None = None,
        interaction_lines: Sequence[str] = (),
        received_at: datetime | None = None,
    ) -> AssembledContext:
        prompt = build_system_prompt(
            "chat",
            self._persona,
            self._templates,
            capabilities=self._capabilities,
            resources=self._resources,
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

        moment = (received_at or self._clock(self._timezone)).astimezone(self._timezone)
        system_text += "\n\n" + DAY_MARKER.format(day=f"{moment:%Y/%m/%d}")

        timeline, notes = self._timeline(moment=moment, include_perception=user_text is not None)
        boundary = (
            max(
                (index for index, line in enumerate(timeline) if line.is_reply),
                default=-1,
            )
            + 1
        )
        messages: list[BaseMessage] = [SystemMessage(content=system_text)]
        if boundary > 0:
            messages.extend(self._render_line(line) for line in timeline[:boundary])
            messages.append(AIMessage(content=CURRENT_SEPARATOR))
            messages.extend(self._render_line(line) for line in timeline[boundary:])
        else:
            messages.extend(self._render_line(line) for line in timeline)
        clock = f"{moment:%H:%M:%S}"
        input_blocks: list[str] = []
        if trigger_reason:
            input_blocks.append(PROACTIVE_TRIGGER.format(reason=trigger_reason, time=clock))
        input_blocks.extend(interaction_lines)
        if user_text:
            input_blocks.append(_stamp(clock, user_text))
        if input_blocks:
            messages.append(HumanMessage(content="\n\n".join(input_blocks)))
        return AssembledContext(
            messages=messages,
            template_digest=prompt.template_digest,
            chars=sum(len(message.content) for message in messages),
            dropped_fragments=dropped,
            warnings=tuple(warnings),
            injected=injected,
            notes=tuple(notes),
        )

    def _render_line(self, line: ChatLine) -> BaseMessage:
        """带角色的历史行统一渲染为 `[role][HH:MM:SS]文本`；会话备注保持原样。"""
        if line.role is None:
            return line.message
        moment = line.at.astimezone(self._timezone)
        text = _stamp(f"{moment:%H:%M:%S}", line.message.content, line.role)
        if line.role == USER_ROLE:
            return HumanMessage(content=text)
        return AIMessage(content=text)

    def _structured_block(self, warnings: list[str]) -> str:
        """结构化记忆全文注入，不做预算裁剪：它是主人手写的大纲，删行会丢语义。"""
        if self._structured is None:
            return ""
        try:
            lines = self._structured.lines()
        except (MemoryReadError, OSError):
            warnings.append("结构化记忆无法读取，已跳过")
            return ""
        return "\n".join(lines).strip()

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

    def _timeline(
        self, *, moment: datetime, include_perception: bool
    ) -> tuple[list[ChatLine], list[ChatLine]]:
        today = moment.date()
        lines: list[ChatLine] = list(self._transcript.lines()) if self._transcript else []
        notes: list[ChatLine] = []
        if self._perception is not None and include_perception:
            for event in self._perception.recent_context_events(CONTEXT_EVENT_LIMIT):
                local = event.at.astimezone(self._timezone)
                if local.date() != today:
                    continue
                notes.append(
                    ChatLine(
                        at=event.at,
                        message=AIMessage(content=f"[感知 {local:%H:%M}] {event.summary}"),
                    )
                )
        timeline = [*lines, *notes]
        timeline.sort(key=lambda line: line.at)
        return timeline, notes
