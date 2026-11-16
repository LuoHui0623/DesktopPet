"""chat 通道的上下文业务编排（`Q5`/`D13`）。

排版、分隔线、时间戳、轮次切分与降级应用由 `service.context` 提供；本模块只负责
业务内容：读人设 / 结构化记忆 / 回忆 / 召回 / 感知 / transcript，决定块顺序与降级顺序，
并把中性的 `ContextMessage` 转换成 LangChain 消息。
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from agent.capabilities.chat.transcript import ChatLine, TranscriptReader
from agent.capabilities.perception.constants import CONTEXT_EVENT_LIMIT
from agent.capabilities.perception.events.events import PerceptionEvent
from agent.memory.contracts import DailyLogEntry
from agent.memory.entry_service import MemoryEntryService, render_entry_lines
from agent.persona.loader import PersonaConfig
from agent.prompts.chat_system import SEPARATOR_WIDTH
from agent.prompts.templates import (
    CapabilityDescription,
    PromptTemplates,
    ResourceItem,
    SystemPrompt,
    build_system_prompt,
)
from service.context import (
    NO_DEGRADATION,
    USER_ROLE,
    ContextEvent,
    ContextLine,
    ContextMessage,
    Degradation,
    assemble,
    current_separator,
    stamp,
)
from service.context import (
    day_marker as _day_marker,
)

logger = logging.getLogger("pet")

# 两条分隔线同宽，让界面上下的分节视觉一致（D5）。
CURRENT_SEPARATOR = current_separator(SEPARATOR_WIDTH)

# 上下文降级告警的稳定 code；only memory_unloaded 会被映射为用户可见 notices（D-30）。
WARNING_MEMORY_UNLOADED = "memory_unloaded"
WARNING_FRAGMENTS_DROPPED = "fragments_dropped"
WARNING_EVENTS_DROPPED = "events_dropped"
WARNING_RECOLLECTION_DROPPED = "recollection_dropped"
WARNING_HISTORY_DROPPED = "history_dropped"


def day_marker(moment: datetime) -> str:
    """日期分隔线：先格式化再居中，保证宽度与 current 分隔线一致。"""
    return _day_marker(moment, SEPARATOR_WIDTH)


PROACTIVE_TRIGGER = (
    "【本次触发】{reason}\n【当前时间】{time}\n现在主动说一句话。不要问问题，不要解释你在主动。"
)


class PerceptionReader(Protocol):
    def recent_context_events(
        self, limit: int = CONTEXT_EVENT_LIMIT
    ) -> Sequence[PerceptionEvent]: ...


@dataclass(frozen=True)
class MemoryFragment:
    """历史召回片段：真实 daily_log 行 id 作提示词内锚点，附角色与原文。

    行 id 只供模型理解来源，不是面向回复的引用键；召回不产生用户可见 sources（第 26 条/T-03）。
    """

    id: int
    role: str
    text: str


@dataclass(frozen=True)
class ContextWarning:
    """上下文降级告警：用 code 区分语义，message 供开发态诊断。"""

    code: str
    message: str


def visible_notices(warnings: Sequence[ContextWarning]) -> tuple[str, ...]:
    """把告警映射成用户可见的系统提示：只有 memory_unloaded 会打扰用户（D-30）。"""
    return tuple(warning.code for warning in warnings if warning.code == WARNING_MEMORY_UNLOADED)


@dataclass(frozen=True)
class AssembledContext:
    messages: list[BaseMessage]
    template_digest: str
    chars: int
    dropped_fragments: int
    warnings: tuple[ContextWarning, ...]
    injected: tuple[MemoryFragment, ...] = ()
    # 可降级块的全量计数（不随降级量变化），供 chat 侧降级循环按绝对口径逐步丢块（T-05）。
    event_count: int = 0
    history_turns: int = 0
    has_recollection: bool = False
    fragment_count: int = 0


def _flatten(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", " ")


def render_fragment(fragment: MemoryFragment) -> str:
    """行 id 锚点 + 角色 + 原文（单行）；不附日期，也不附面向回复的引用指令（第 26 条/T-03）。"""
    return f"[#{fragment.id}][{fragment.role}] {_flatten(fragment.text)}"


def _drop_adjacent_query(timeline: list[ChatLine], current_text: str) -> list[ChatLine]:
    """连续两轮相同 query 只在模型上下文里保留较新一条（D-06/D-20）。

    只与最近一条主人发言比较；中间隔着助手回复或感知事件不影响「相邻」判定。
    """
    target = current_text.strip()
    for index in range(len(timeline) - 1, -1, -1):
        line = timeline[index]
        if line.role != USER_ROLE:
            continue
        if line.message.content.strip() == target:
            return [*timeline[:index], *timeline[index + 1 :]]
        return timeline
    return timeline


def _context_line(line: ChatLine, timezone: tzinfo) -> ContextLine:
    """把 transcript 行转换为中性装配输入；角色为空时按原始消息类型保留角色语义。"""
    kind = "human" if isinstance(line.message, HumanMessage) else "ai"
    return ContextLine(
        at=line.at.astimezone(timezone),
        text=line.message.content,
        role=line.role,
        kind=kind,
    )


def _to_message(item: ContextMessage) -> BaseMessage:
    if item.role == "system":
        return SystemMessage(content=item.content)
    if item.role == "human":
        return HumanMessage(content=item.content)
    return AIMessage(content=item.content)


class ContextBuilder:
    def __init__(
        self,
        persona: PersonaConfig,
        templates: PromptTemplates,
        timezone: tzinfo,
        *,
        memory: MemoryEntryService | None = None,
        clock: Callable[[tzinfo], datetime] | None = None,
        capabilities: Sequence[CapabilityDescription] = (),
        resources: Sequence[ResourceItem] | None = None,
        perception: PerceptionReader | None = None,
        transcript: TranscriptReader | None = None,
        base_prompt: SystemPrompt | None = None,
        recollection: str | None = None,
    ) -> None:
        self._persona = persona
        self._templates = templates
        self._timezone = timezone
        self._memory = memory
        self._clock = clock or (lambda zone: datetime.now(zone))
        self._capabilities = tuple(capabilities)
        self._resources = resources
        self._perception = perception
        self._transcript = transcript
        # chat 服务可先构建系统提示词再交给构建器，避免在此处重复渲染人设与占位符。
        self._base_prompt = base_prompt
        # 回忆正文由 chat 服务等出来再交进来，注入位置固定在日期分隔线之后。
        self._recollection = recollection

    def build(
        self,
        user_text: str | None = None,
        fragments: Sequence[MemoryFragment] = (),
        *,
        trigger_reason: str | None = None,
        interaction_lines: Sequence[str] = (),
        received_at: datetime | None = None,
        current_record: DailyLogEntry | None = None,
        degradation: Degradation = NO_DEGRADATION,
    ) -> AssembledContext:
        prompt = self._base_prompt or build_system_prompt(
            "chat",
            self._persona,
            self._templates,
            capabilities=self._capabilities,
            resources=self._resources,
        )
        warnings: list[ContextWarning] = []
        moment = (received_at or self._clock(self._timezone)).astimezone(self._timezone)
        today = moment.date()

        structured_block = self._structured_block(warnings)

        # 召回片段不设独立字符预算（Q6/T-03）：全量注入，超窗只由模型窗口检查与 T-05 降级兜底。
        # 降级时按相关度高→低保留前 N 条，从尾部丢低相关片段（Q31/T-05）。
        kept = list(fragments)
        if degradation.keep_fragments is not None:
            kept = kept[: degradation.keep_fragments]
        fragment_lines = [render_fragment(fragment) for fragment in kept]

        # 历史只来自落库记录；本轮记录按身份从历史中剔除，再由读回内容作为 current 输入。
        timeline = self._history(today, current_record)
        if current_record is not None:
            timeline = _drop_adjacent_query(timeline, current_record.content)
        history = [_context_line(line, self._timezone) for line in timeline]

        # current 区：感知事件在前，本轮输入最后（D-12）。降级时整块丢弃感知事件（T-05）。
        current_events = self._current_events(today) if user_text is not None else ()

        clock = f"{moment:%H:%M:%S}"
        input_blocks: list[str] = []
        if trigger_reason:
            input_blocks.append(PROACTIVE_TRIGGER.format(reason=trigger_reason, time=clock))
        input_blocks.extend(interaction_lines)
        if current_record is not None:
            input_blocks.append(self._render_current_record(current_record))
        elif user_text:
            input_blocks.append(stamp(clock, user_text))

        assembled = assemble(
            base_prompt=prompt.text,
            moment=moment,
            separator_width=SEPARATOR_WIDTH,
            recollection=self._recollection,
            structured_block=structured_block,
            fragment_lines=fragment_lines,
            history=history,
            current_events=current_events,
            current_blocks=input_blocks,
            degradation=degradation,
        )
        return AssembledContext(
            messages=[_to_message(item) for item in assembled.messages],
            template_digest=prompt.template_digest,
            chars=assembled.chars,
            dropped_fragments=len(fragments) - len(kept),
            warnings=tuple(warnings),
            injected=tuple(kept),
            event_count=assembled.event_count,
            history_turns=assembled.history_turns,
            has_recollection=assembled.has_recollection,
            fragment_count=len(fragments),
        )

    def _history(self, day: date, current_record: DailyLogEntry | None) -> list[ChatLine]:
        """按本轮固定日期读回历史，并按身份剔除本轮记录（D-03/D-07）。"""
        if self._transcript is None:
            return []
        lines = list(self._transcript.lines(day))
        if current_record is None or current_record.id is None:
            return lines
        return [line for line in lines if line.record_id != current_record.id]

    def _current_events(self, today: date) -> tuple[ContextEvent, ...]:
        """本轮感知事件：当天、上限 5 条、按发生时间排序；读取失败静默跳过（D-19）。"""
        if self._perception is None:
            return ()
        try:
            events = self._perception.recent_context_events(CONTEXT_EVENT_LIMIT)
        except Exception:
            logger.exception("感知事件读取失败，本轮跳过")
            return ()
        picked: list[ContextEvent] = []
        for event in events:
            local = event.at.astimezone(self._timezone)
            if local.date() != today:
                continue
            picked.append(ContextEvent(at=local, summary=event.summary))
        picked.sort(key=lambda item: item.at)
        return tuple(picked)

    def _render_current_record(self, record: DailyLogEntry) -> str:
        moment = record.event_time.astimezone(self._timezone)
        return stamp(f"{moment:%H:%M:%S}", record.content)

    def _structured_block(self, warnings: list[ContextWarning]) -> str:
        """结构化条目全量注入：按有序 tags 组装 Markdown 树，不设上限、不静默截断。

        读取不传 ``limit``，窗口开销只由聊天侧现有的 token 检查兜底（Q6/Q23）；
        超限逐块降级归 T-05，这里不做裁剪。
        """
        if self._memory is None:
            return ""
        try:
            lines = render_entry_lines(self._memory.load(), include_disabled=False)
        except Exception:
            logger.exception("结构化条目读取失败，本轮跳过")
            warnings.append(ContextWarning(WARNING_MEMORY_UNLOADED, "结构化记忆无法读取，已跳过"))
            return ""
        return "\n".join(lines).strip()
