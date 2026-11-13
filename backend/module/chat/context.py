import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from config.persona import PersonaConfig
from constants.memory import FRAGMENT_TOKEN_BUDGET
from module.agent.chat_system_prompt import SEPARATOR_WIDTH
from module.agent.prompt import (
    CapabilityDescription,
    PromptTemplates,
    ResourceItem,
    SystemPrompt,
    build_system_prompt,
)
from module.chat.transcript import ChatLine, TranscriptReader
from module.memory.contracts import DailyLogEntry
from module.memory.entry_service import MemoryEntryService, render_entry_lines
from service.perception.constants import CONTEXT_EVENT_LIMIT
from service.perception.events import PerceptionEvent

logger = logging.getLogger("pet")

KIND_STRUCTURED = "结构化记忆"
KIND_DAILY = "每日对话"
# 两条分隔线同宽，让界面上下的分节视觉一致（D5）。
CURRENT_SEPARATOR = "current".center(SEPARATOR_WIDTH, "=")

# 上下文降级告警的稳定 code；only memory_unloaded 会被映射为用户可见 notices（D-30）。
WARNING_MEMORY_UNLOADED = "memory_unloaded"
WARNING_FRAGMENTS_DROPPED = "fragments_dropped"


def day_marker(moment: datetime) -> str:
    """日期分隔线：先格式化再居中，保证宽度与 current 分隔线一致。"""
    return f"{moment:%Y/%m/%d}".center(SEPARATOR_WIDTH, "=")


USER_ROLE = "master"
EVENT_ROLE = "event"
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


def _flatten(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", " ")


def _stamp(clock: str, text: str, role: str = USER_ROLE) -> str:
    """给发言加角色与秒级时间前缀：`[role][HH:MM:SS]对话`。"""
    return f"[{role}][{clock}]{text}"


def render_fragment(fragment: MemoryFragment) -> str:
    tags = (fragment.kind, fragment.date)
    return f"[{' · '.join(tags)}] {_flatten(fragment.text)}"


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

        # 日期分隔符紧跟基础提示词：全局只出现这一次，之前是注入段落。
        system_text = prompt.text + "\n\n" + day_marker(moment)

        if self._recollection:
            system_text += f"\n\n【昨天的回忆】\n{self._recollection}"

        structured_block = self._structured_block(warnings)
        if structured_block:
            system_text += f"\n\n【结构化记忆】\n{structured_block}"

        kept, dropped = self._fit_fragments(fragments)
        if dropped:
            warnings.append(
                ContextWarning(
                    WARNING_FRAGMENTS_DROPPED,
                    f"检索到的记忆超出预算，已舍弃 {dropped} 条",
                )
            )
        if kept:
            fragment_block = "\n".join(render_fragment(fragment) for fragment in kept)
            system_text += f"\n\n【检索到的记忆】\n{fragment_block}"

        injected = tuple(kept)

        # 历史只来自落库记录；本轮记录按身份从历史中剔除，再由读回内容作为 current 输入。
        timeline = self._history(today, current_record)
        if current_record is not None:
            timeline = _drop_adjacent_query(timeline, current_record.content)

        # 历史区：当天全部聊天记录，不按角色扫描分界（D-18）。
        messages: list[BaseMessage] = [SystemMessage(content=system_text)]
        messages.extend(self._render_line(line) for line in timeline)
        if timeline:
            messages.append(AIMessage(content=CURRENT_SEPARATOR))

        # current 区：感知事件在前，本轮输入最后（D-12）。
        if user_text is not None:
            messages.extend(self._render_event(event) for event in self._current_events(today))

        clock = f"{moment:%H:%M:%S}"
        input_blocks: list[str] = []
        if trigger_reason:
            input_blocks.append(PROACTIVE_TRIGGER.format(reason=trigger_reason, time=clock))
        input_blocks.extend(interaction_lines)
        if current_record is not None:
            input_blocks.append(self._render_current_record(current_record))
        elif user_text:
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
        )

    def _history(self, day: date, current_record: DailyLogEntry | None) -> list[ChatLine]:
        """按本轮固定日期读回历史，并按身份剔除本轮记录（D-03/D-07）。"""
        if self._transcript is None:
            return []
        lines = list(self._transcript.lines(day))
        if current_record is None or current_record.id is None:
            return lines
        return [line for line in lines if line.record_id != current_record.id]

    def _current_events(self, today: date) -> tuple[tuple[datetime, str], ...]:
        """本轮感知事件：当天、上限 5 条、按发生时间排序；读取失败静默跳过（D-19）。"""
        if self._perception is None:
            return ()
        try:
            events = self._perception.recent_context_events(CONTEXT_EVENT_LIMIT)
        except Exception:
            logger.exception("感知事件读取失败，本轮跳过")
            return ()
        picked: list[tuple[datetime, str]] = []
        for event in events:
            local = event.at.astimezone(self._timezone)
            if local.date() != today:
                continue
            picked.append((event.at, event.summary))
        picked.sort(key=lambda item: item[0])
        return tuple(picked)

    def _render_event(self, event: tuple[datetime, str]) -> BaseMessage:
        at, summary = event
        local = at.astimezone(self._timezone)
        return AIMessage(content=_stamp(f"{local:%H:%M:%S}", summary, EVENT_ROLE))

    def _render_current_record(self, record: DailyLogEntry) -> str:
        moment = record.event_time.astimezone(self._timezone)
        return _stamp(f"{moment:%H:%M:%S}", record.content)

    def _render_line(self, line: ChatLine) -> BaseMessage:
        """带角色的历史行统一渲染为 `[role][HH:MM:SS]文本`。"""
        if line.role is None:
            return line.message
        moment = line.at.astimezone(self._timezone)
        text = _stamp(f"{moment:%H:%M:%S}", line.message.content, line.role)
        if line.role == USER_ROLE:
            return HumanMessage(content=text)
        return AIMessage(content=text)

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
