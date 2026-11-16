"""通用上下文消息装配机制（`Q5`/`D13`）。

只负责排版：系统提示词、日期与 current 分隔线、角色与时间戳、历史/本轮拼装、
轮次切分、降级应用与字符计数。所有数据由业务域传入；本模块不读 agent 域，
也不依赖 LangChain（返回中性的角色与文本）。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

USER_ROLE = "master"
EVENT_ROLE = "event"


@dataclass(frozen=True)
class ContextLine:
    """历史行：`kind` 决定消息角色，`role` 为提示词内的展示角色。"""

    at: datetime
    text: str
    role: str | None = None
    kind: str = "human"


@dataclass(frozen=True)
class ContextEvent:
    """本轮感知事件：时间已由调用方换算到本地时区。"""

    at: datetime
    summary: str


@dataclass(frozen=True)
class ContextMessage:
    role: str
    content: str


@dataclass(frozen=True)
class Degradation:
    """超窗降级规格：按优先级丢弃可降级块，必保块不受影响。"""

    drop_events: bool = False
    drop_recollection: bool = False
    keep_fragments: int | None = None
    keep_history_turns: int | None = None


NO_DEGRADATION = Degradation()


@dataclass(frozen=True)
class AssembledMessages:
    messages: list[ContextMessage]
    chars: int
    event_count: int
    history_turns: int
    has_recollection: bool


def day_marker(moment: datetime, width: int) -> str:
    """日期分隔线：先格式化再居中，保证与 current 分隔线同宽。"""
    return f"{moment:%Y/%m/%d}".center(width, "=")


def current_separator(width: int) -> str:
    return "current".center(width, "=")


def stamp(clock: str, text: str, role: str = USER_ROLE) -> str:
    """给发言加角色与秒级时间前缀：`[role][HH:MM:SS]对话`。"""
    return f"[{role}][{clock}]{text}"


def split_turns(lines: Sequence[ContextLine]) -> list[list[ContextLine]]:
    """按主人发言切轮：每条 master 起一新轮，其余行归入当前轮。"""
    turns: list[list[ContextLine]] = []
    for line in lines:
        if line.role == USER_ROLE or not turns:
            turns.append([line])
        else:
            turns[-1].append(line)
    return turns


def assemble(
    *,
    base_prompt: str,
    moment: datetime,
    separator_width: int,
    recollection: str | None,
    structured_block: str,
    fragment_lines: Sequence[str],
    history: Sequence[ContextLine],
    current_events: Sequence[ContextEvent],
    current_blocks: Sequence[str],
    degradation: Degradation = NO_DEGRADATION,
) -> AssembledMessages:
    """把业务域准备好的内容装配为有序消息。

    调用方负责：提示词内容、记忆与感知读取、块顺序、片段裁剪与降级顺序；
    时间统一换算到本地时区后再传入。
    """
    system_text = base_prompt + "\n\n" + day_marker(moment, separator_width)
    if recollection and not degradation.drop_recollection:
        system_text += f"\n\n【昨天的回忆】\n{recollection}"
    if structured_block:
        system_text += f"\n\n【结构化记忆】\n{structured_block}"
    if fragment_lines:
        system_text += "\n\n【检索到的记忆】\n" + "\n".join(fragment_lines)

    turns = split_turns(history)
    history_turns = len(turns)
    if degradation.keep_history_turns is not None:
        keep = degradation.keep_history_turns
        turns = turns[-keep:] if keep > 0 else []
    timeline = [line for turn in turns for line in turn]

    messages: list[ContextMessage] = [ContextMessage("system", system_text)]
    messages.extend(_render_line(line) for line in timeline)
    if timeline:
        messages.append(ContextMessage("ai", current_separator(separator_width)))

    if not degradation.drop_events:
        messages.extend(_render_event(event) for event in current_events)

    if current_blocks:
        messages.append(ContextMessage("human", "\n\n".join(current_blocks)))
    return AssembledMessages(
        messages=messages,
        chars=sum(len(message.content) for message in messages),
        event_count=len(current_events),
        history_turns=history_turns,
        has_recollection=bool(recollection),
    )


def _render_line(line: ContextLine) -> ContextMessage:
    if line.role is None:
        return ContextMessage(line.kind, line.text)
    text = stamp(f"{line.at:%H:%M:%S}", line.text, line.role)
    return ContextMessage("human" if line.role == USER_ROLE else "ai", text)


def _render_event(event: ContextEvent) -> ContextMessage:
    return ContextMessage("ai", stamp(f"{event.at:%H:%M:%S}", event.summary, EVENT_ROLE))
