from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from config.persona import parse_persona
from module.agent.prompt import load_templates
from module.chat import context as context_module
from module.chat.context import (
    CURRENT_SEPARATOR,
    KIND_DAILY,
    PROACTIVE_TRIGGER,
    ContextBuilder,
    MemoryFragment,
)
from module.chat.transcript import ChatLine, ChatTranscript, reply_message
from module.memory.memory_service import MemoryReadError, StructuredMemoryDoc
from service.perception.events import PerceptionEvent, PerceptionEventKind

TZ = ZoneInfo("Asia/Shanghai")
PERSONA = parse_persona({"role": {"名字": "小桌", "人设": "陪伴"}, "personality": ["温和"]})


def at(hour: int, minute: int = 0, day: int = 18) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def master(moment: datetime, text: str) -> ChatLine:
    return ChatLine(at=moment, message=HumanMessage(content=text))


def me(moment: datetime, text: str) -> ChatLine:
    return ChatLine(at=moment, message=reply_message(text), is_reply=True)


def note(moment: datetime, text: str) -> ChatLine:
    return ChatLine(at=moment, message=AIMessage(content=text))


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


class FakePerception:
    def __init__(self, events: list[PerceptionEvent]) -> None:
        self.events = events

    def recent_context_events(self, limit: int = 3) -> tuple[PerceptionEvent, ...]:
        return tuple(self.events[-limit:])


def pe(kind: PerceptionEventKind, hour: int, minute: int, **fields) -> PerceptionEvent:
    return PerceptionEvent(kind=kind, at=at(hour, minute), **fields)


@pytest.fixture
def parts(tmp_path: Path):
    clock = Clock(at(21, 30))
    structured = StructuredMemoryDoc(tmp_path / "memory" / "structured.md")

    def transcript(*lines: ChatLine) -> ChatTranscript:
        item = ChatTranscript(TZ, clock=clock)
        for line in lines:
            item.add(line)
        return item

    def builder(*, transcript_: ChatTranscript | None = None, perception=None) -> ContextBuilder:
        return ContextBuilder(
            PERSONA,
            load_templates(),
            TZ,
            structured=structured,
            clock=clock,
            perception=perception,
            transcript=transcript_,
        )

    return {
        "clock": clock,
        "structured": structured,
        "transcript": transcript,
        "builder": builder,
    }


def test_persona_is_first_then_structured_then_input(parts):
    parts["structured"].save("## 喜好\n喜欢美式咖啡，不加糖")
    result = parts["builder"]().build("你好")
    system = result.messages[0]
    assert isinstance(system, SystemMessage)
    assert system.content.index("【人设】") < system.content.index("【结构化记忆】")
    assert "喜欢美式咖啡，不加糖" in system.content
    assert result.messages[-1] == HumanMessage(content="你好")


def test_proactive_build_appends_trigger_block_and_skips_perception(parts):
    perception = FakePerception([pe(PerceptionEventKind.APP_IN_USE, 21, 20, exe="code", minutes=3)])
    result = parts["builder"](perception=perception).build(trigger_reason="使用 code 已 3 分钟")

    assert result.messages[-1] == HumanMessage(
        content=PROACTIVE_TRIGGER.format(reason="使用 code 已 3 分钟")
    )
    assert result.notes == ()
    assert all("[感知" not in message.content for message in result.messages)


def test_structured_block_is_omitted_when_document_is_empty(parts):
    parts["structured"].save("")
    text = parts["builder"]().build("你好").messages[0].content
    assert "【结构化记忆】" not in text


def test_unreadable_structured_memory_is_skipped_with_warning(parts, monkeypatch):
    def explode() -> list[str]:
        raise MemoryReadError("坏了")

    monkeypatch.setattr(parts["structured"], "lines", explode)
    result = parts["builder"]().build("你好")
    assert "【结构化记忆】" not in result.messages[0].content
    assert any("结构化记忆无法读取" in warning for warning in result.warnings)


def test_structured_over_budget_is_truncated_with_warning(parts, monkeypatch):
    parts["structured"].save("## 喜好\n" + "字" * 400)
    monkeypatch.setattr(context_module, "STRUCTURED_MEMORY_TOKEN_BUDGET", 20)
    result = parts["builder"]().build("你好")
    text = result.messages[0].content
    assert "……（已截断）" in text
    assert any("结构化记忆超出预算" in warning for warning in result.warnings)


def test_retrieved_fragments_are_labelled(parts):
    fragments = [
        MemoryFragment(kind=KIND_DAILY, date="2026-10-18", text="早安\n你好"),
    ]
    text = parts["builder"]().build("你好", fragments).messages[0].content
    assert "【检索到的记忆】" in text
    assert "[每日对话 · 2026-10-18] 早安 你好" in text


def test_transcript_lines_follow_construction_order(parts):
    lines = (
        master(at(9), "一"),
        me(at(9), "二"),
        master(at(10), "三"),
        me(at(10), "四"),
    )
    messages = parts["builder"](transcript_=parts["transcript"](*lines)).build("五").messages
    assert [type(message) for message in messages[1:-1]] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
        AIMessage,
        AIMessage,
    ]
    assert [message.content for message in messages[1:-1]] == [
        "一",
        '{"type": "chat", "text": "二"}',
        "三",
        '{"type": "chat", "text": "四"}',
        CURRENT_SEPARATOR,
    ]


def test_appended_event_lines_stay_assistant_messages(parts):
    lines = (
        master(at(9), "一"),
        me(at(9), "二"),
        note(at(9, 5), "[事件] 派发失败：记忆检索，超时"),
        master(at(9, 10), "三"),
    )
    messages = parts["builder"](transcript_=parts["transcript"](*lines)).build("你好").messages
    assert [type(message) for message in messages[1:-1]] == [
        HumanMessage,
        AIMessage,
        AIMessage,
        AIMessage,
        HumanMessage,
    ]
    assert messages[3].content == CURRENT_SEPARATOR
    assert messages[4].content == "[事件] 派发失败：记忆检索，超时"


def test_event_before_any_message_is_kept(parts):
    lines = (note(at(9), "[事件] 审批被拒绝：记忆写入，未确认"),)
    messages = parts["builder"](transcript_=parts["transcript"](*lines)).build("你好").messages
    assert [message.content for message in messages[1:-1]] == [
        "[事件] 审批被拒绝：记忆写入，未确认"
    ]
    assert isinstance(messages[1], AIMessage)


def test_standalone_me_and_consecutive_roles_are_kept(parts):
    lines = (
        me(at(9), "先开口"),
        master(at(9, 1), "一"),
        master(at(9, 2), "二"),
        me(at(9, 3), "三"),
        me(at(9, 4), "四"),
    )
    messages = parts["builder"](transcript_=parts["transcript"](*lines)).build("你好").messages
    assert [type(message) for message in messages[1:-1]] == [
        AIMessage,
        HumanMessage,
        HumanMessage,
        AIMessage,
        AIMessage,
        AIMessage,
    ]
    assert messages[-2].content == CURRENT_SEPARATOR


def test_new_day_clears_the_transcript(parts):
    snapshot = parts["transcript"](master(at(23), "昨天的话"), me(at(23), "好"))
    parts["clock"].moment = at(8, day=19)
    assert snapshot.lines() == ()
    messages = parts["builder"](transcript_=snapshot).build("早").messages
    assert [message.content for message in messages[1:-1]] == []


def test_full_transcript_is_kept_without_trimming(parts):
    lines = tuple(
        line
        for index in range(20)
        for line in (
            master(at(9, index), f"第{index}轮" + "问" * 200),
            me(at(9, index), "答" * 200),
        )
    )
    result = parts["builder"](transcript_=parts["transcript"](*lines)).build("现在")
    assert any(message.content.startswith("第0轮") for message in result.messages)
    assert any(message.content.startswith("第19轮") for message in result.messages)
    assert not any("已裁剪" in warning for warning in result.warnings)


def test_fragments_over_budget_are_dropped_and_counted(parts, monkeypatch):
    monkeypatch.setattr(context_module, "FRAGMENT_TOKEN_BUDGET", 40)
    fragments = [MemoryFragment(kind=KIND_DAILY, date="2026-10-18", text="字" * 20)] * 4
    result = parts["builder"]().build("你好", fragments)
    assert result.dropped_fragments > 0
    assert any("已舍弃" in warning for warning in result.warnings)


def test_result_reports_template_digest_and_size(parts):
    result = parts["builder"]().build("你好")
    assert len(result.template_digest) == 64
    assert result.chars == sum(len(message.content) for message in result.messages)


def test_injected_lists_kept_fragments_only(parts):
    fragments = [
        MemoryFragment(kind=KIND_DAILY, date="2026-10-18", text="字" * 20),
        MemoryFragment(kind=KIND_DAILY, date="2026-10-17", text="字" * 2000),
    ]
    result = parts["builder"]().build("你好", fragments)
    assert [fragment.date for fragment in result.injected] == ["2026-10-18"]
    assert result.dropped_fragments == 1
    assert context_module.CITATION_RULE in result.messages[0].content


def test_structured_memory_is_not_citable(parts):
    parts["structured"].save("## 喜好\n喜欢茶")
    result = parts["builder"]().build("你好")
    assert result.injected == ()
    assert "【结构化记忆】" in result.messages[0].content
    assert context_module.CITATION_RULE not in result.messages[0].content


def test_citation_rule_is_omitted_when_nothing_is_injected(parts):
    result = parts["builder"]().build("你好")
    assert result.injected == ()
    assert context_module.CITATION_RULE not in result.messages[0].content


def test_perception_after_last_reply_is_injected_after_separator(parts):
    lines = (master(at(9), "一"), me(at(9, 1), "二"))
    perception = FakePerception(
        [
            pe(PerceptionEventKind.APP_IN_USE, 9, 30, exe="code", minutes=5),
            pe(PerceptionEventKind.PRESENCE_RETURN, 9, 40, minutes=12),
        ]
    )
    messages = (
        parts["builder"](transcript_=parts["transcript"](*lines), perception=perception)
        .build("你好")
        .messages
    )
    assert [type(message) for message in messages[1:-1]] == [
        HumanMessage,
        AIMessage,
        AIMessage,
        AIMessage,
        AIMessage,
    ]
    assert messages[3].content == CURRENT_SEPARATOR
    assert messages[4].content == "[感知 09:30] 使用 code 已 5 分钟"
    assert messages[5].content == "[感知 09:40] 离开 12 分钟后回来"


def test_perception_notes_are_returned_for_the_transcript(parts):
    perception = FakePerception([pe(PerceptionEventKind.APP_IN_USE, 9, 30, exe="code", minutes=5)])
    result = parts["builder"](perception=perception).build("你好")
    assert [line.message.content for line in result.notes] == ["[感知 09:30] 使用 code 已 5 分钟"]
    assert result.notes[0].at == at(9, 30)


def test_perception_injection_is_capped_at_three(parts):
    perception = FakePerception(
        [
            pe(PerceptionEventKind.APP_IN_USE, 8, 0, exe="a", minutes=3),
            pe(PerceptionEventKind.APP_IN_USE, 8, 10, exe="b", minutes=3),
            pe(PerceptionEventKind.APP_IN_USE, 8, 20, exe="c", minutes=3),
            pe(PerceptionEventKind.APP_IN_USE, 8, 30, exe="d", minutes=3),
            pe(PerceptionEventKind.APP_IN_USE, 8, 40, exe="e", minutes=3),
        ]
    )
    messages = parts["builder"](perception=perception).build("你好").messages
    notes = [message.content for message in messages if "[感知" in message.content]
    assert len(notes) == 3
    assert "使用 e" in notes[-1]


def test_perception_note_from_an_earlier_turn_stays_in_history(parts):
    lines = (
        master(at(9), "一"),
        note(at(9, 2), "[感知 09:01] 打开 vscode"),
        me(at(9, 5), "二"),
    )
    perception = FakePerception([])
    messages = (
        parts["builder"](transcript_=parts["transcript"](*lines), perception=perception)
        .build("你好")
        .messages
    )
    assert messages[2].content == "[感知 09:01] 打开 vscode"
    assert messages[-2].content == CURRENT_SEPARATOR


def test_no_reply_yet_means_everything_is_current(parts):
    lines = (master(at(9), "一"),)
    perception = FakePerception([pe(PerceptionEventKind.APP_IN_USE, 9, 30, exe="code", minutes=5)])
    messages = (
        parts["builder"](transcript_=parts["transcript"](*lines), perception=perception)
        .build("你好")
        .messages
    )
    assert not any(message.content == CURRENT_SEPARATOR for message in messages)


def test_perception_from_another_day_is_ignored(parts):
    perception = FakePerception(
        [
            PerceptionEvent(
                kind=PerceptionEventKind.APP_IN_USE,
                at=at(9, 30, day=17),
                exe="code",
                minutes=5,
            )
        ]
    )
    messages = parts["builder"](perception=perception).build("你好").messages
    assert not any("[感知" in message.content for message in messages)
