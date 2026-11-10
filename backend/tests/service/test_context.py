from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from config.persona import parse_persona
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.chat_system_prompt import SEPARATOR_WIDTH
from module.agent.prompt import ResourceItem, SystemPrompt, load_templates
from module.chat import context as context_module
from module.chat.context import (
    CURRENT_SEPARATOR,
    KIND_DAILY,
    PROACTIVE_TRIGGER,
    WARNING_FRAGMENTS_DROPPED,
    WARNING_MEMORY_UNLOADED,
    ContextBuilder,
    MemoryFragment,
    day_marker,
    visible_notices,
)
from module.chat.transcript import ChatLine
from module.memory.contracts import DailyLogEntry, MemoryEntryCreate
from module.memory.entry_service import MemoryEntryService
from module.memory.memory_service import MemoryReadError
from service.perception.events import PerceptionEvent, PerceptionEventKind

TZ = ZoneInfo("Asia/Shanghai")
PERSONA = parse_persona({"role": {"名字": "小桌", "人设": "陪伴"}, "personality": ["温和"]})


def at(hour: int, minute: int = 0, day: int = 18, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=TZ)


def master(moment: datetime, text: str, identity: int | None = None) -> ChatLine:
    return ChatLine(
        at=moment,
        message=HumanMessage(content=text),
        role="master",
        record_id=identity,
        record_day=moment.date(),
    )


def me(moment: datetime, text: str, identity: int | None = None) -> ChatLine:
    return ChatLine(
        at=moment,
        message=AIMessage(content=text),
        is_reply=True,
        role="me",
        record_id=identity,
        record_day=moment.date(),
    )


def assistant(moment: datetime, text: str, identity: int | None = None) -> ChatLine:
    return ChatLine(
        at=moment,
        message=AIMessage(content=text),
        is_reply=True,
        role="assistant",
        record_id=identity,
        record_day=moment.date(),
    )


def record(moment: datetime, text: str, identity: int) -> DailyLogEntry:
    return DailyLogEntry(
        event_time=moment,
        role="master",
        content=text,
        created_at=moment,
        persona_id="小桌",
        id=identity,
    )


def expects(moment: datetime, role: str, text: str) -> str:
    """历史行的渲染结果：带角色与秒级时间前缀。"""
    return f"[{role}][{moment:%H:%M:%S}]{text}"


def day_line(text: str, day: str = "2026/10/18") -> str:
    """抽出系统提示词里的日期分隔线；断言全文只有一行带日期。"""
    lines = [line for line in text.splitlines() if day in line]
    assert len(lines) == 1, lines
    return lines[0]


class Reader:
    """只返回固定行，避免上下文测试依赖 ChatTranscript 的日切逻辑。"""

    def __init__(self, lines: list[ChatLine]) -> None:
        self._lines = lines
        self.days: list[object] = []

    def lines(self, day: object = None) -> tuple[ChatLine, ...]:
        self.days.append(day)
        return tuple(self._lines)


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
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    entries = MemoryEntryService(db_file)

    def add(content: str, tags: list[str] | None = None):
        return entries.add(MemoryEntryCreate(content=content, tags=tags or ["喜好"])).entry

    def transcript(*lines: ChatLine) -> Reader:
        return Reader(list(lines))

    def builder(*, transcript_: Reader | None = None, perception=None) -> ContextBuilder:
        return ContextBuilder(
            PERSONA,
            load_templates(),
            TZ,
            memory=entries,
            clock=clock,
            perception=perception,
            transcript=transcript_,
        )

    return {
        "clock": clock,
        "entries": entries,
        "add": add,
        "transcript": transcript,
        "builder": builder,
    }


def test_resources_render_registered_ids(parts):
    resources = [ResourceItem("草稿本", "draft")]
    builder = ContextBuilder(
        PERSONA, load_templates(), TZ, resources=resources, clock=parts["clock"]
    )
    text = builder.build("你好").messages[0].content
    assert "- 草稿本: draft" in text


def test_persona_is_first_then_structured_then_input(parts):
    parts["add"]("喜欢美式咖啡，不加糖")
    result = parts["builder"]().build("你好")
    system = result.messages[0]
    assert isinstance(system, SystemMessage)
    assert system.content.index("【人设】") < system.content.index("【结构化记忆】")
    assert "喜欢美式咖啡，不加糖" in system.content
    assert result.messages[-1] == HumanMessage(content="[master][21:30:00]你好")


def test_current_input_carries_the_received_second(parts):
    """本次输入也要带秒级接收时间，模型才知道这句话发生在什么时候。"""
    result = parts["builder"]().build("在吗")
    assert result.messages[-1] == HumanMessage(content="[master][21:30:00]在吗")


def test_day_marker_and_input_follow_the_received_time(parts):
    received = datetime(2026, 10, 17, 23, 59, 58, tzinfo=TZ)
    result = parts["builder"]().build("还没睡", received_at=received)
    assert result.messages[-1] == HumanMessage(content="[master][23:59:58]还没睡")
    assert len(day_line(result.messages[0].content, "2026/10/17")) == SEPARATOR_WIDTH


def test_proactive_build_appends_trigger_block_and_skips_perception(parts):
    perception = FakePerception([pe(PerceptionEventKind.APP_IN_USE, 21, 20, exe="code", minutes=3)])
    result = parts["builder"](perception=perception).build(trigger_reason="使用 code 已 3 分钟")

    assert result.messages[-1] == HumanMessage(
        content=PROACTIVE_TRIGGER.format(reason="使用 code 已 3 分钟", time="21:30:00")
    )
    assert result.warnings == ()
    assert all("[event]" not in message.content for message in result.messages)


def test_structured_block_is_omitted_when_document_is_empty(parts):
    text = parts["builder"]().build("你好").messages[0].content
    assert "【结构化记忆】" not in text


def test_unreadable_structured_memory_is_skipped_with_warning(parts, monkeypatch):
    def explode(**kwargs):
        raise MemoryReadError("坏了")

    monkeypatch.setattr(parts["entries"], "load", explode)
    result = parts["builder"]().build("你好")
    assert "【结构化记忆】" not in result.messages[0].content
    assert [warning.code for warning in result.warnings] == [WARNING_MEMORY_UNLOADED]


def test_structured_memory_is_injected_in_full_without_truncation(parts):
    for index in range(50):
        parts["add"](f"第{index}条：" + "字" * 40)
    result = parts["builder"]().build("你好")
    text = result.messages[0].content
    assert all(f"第{index}条：" in text for index in range(50))
    assert "已截断" not in text
    assert result.warnings == ()


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
        expects(at(9), "master", "一"),
        expects(at(9), "me", "二"),
        expects(at(10), "master", "三"),
        expects(at(10), "me", "四"),
        CURRENT_SEPARATOR,
    ]


def test_user_history_carries_role_and_second_level_time(parts):
    lines = (master(at(9, 5), "早"), me(at(9, 6), "早呀"))
    messages = parts["builder"](transcript_=parts["transcript"](*lines)).build("在吗").messages
    assert messages[1].content == "[master][09:05:00]早"
    assert messages[2].content == "[me][09:06:00]早呀"


def test_assistant_history_is_rendered_as_plain_text_with_its_own_role(parts):
    lines = (
        master(at(9), "查一下"),
        assistant(at(9, second=1), "查到咖啡偏好"),
        me(at(9, second=2), "记下啦"),
    )
    messages = parts["builder"](transcript_=parts["transcript"](*lines)).build("在吗").messages
    assert messages[1].content == "[master][09:00:00]查一下"
    assert messages[2].content == "[assistant][09:00:01]查到咖啡偏好"
    assert messages[3].content == "[me][09:00:02]记下啦"
    assert all("type" not in message.content for message in messages[1:4])


def test_several_utterances_are_restored_as_separate_history_lines(parts):
    lines = (
        master(at(9), "你好"),
        me(at(9, second=1), "第一句。"),
        me(at(9, second=2), "第二句！"),
    )
    built = parts["builder"](transcript_=parts["transcript"](*lines)).build("在吗")
    contents = [message.content for message in built.messages[1:-1]]
    assert contents[1:3] == [
        "[me][09:00:01]第一句。",
        "[me][09:00:02]第二句！",
    ]


def test_day_marker_follows_the_base_prompt_and_precedes_injected_blocks(parts):
    """段落顺序：基础系统提示词 → 日期分隔符 → 结构化记忆 → 检索片段 → 引用规则。"""
    parts["add"]("喜欢美式咖啡")
    fragments = [MemoryFragment(kind=KIND_DAILY, date="2026-10-17", text="昨天聊了茶")]
    text = parts["builder"]().build("你好", fragments).messages[0].content
    order = [
        "【人设】",
        "2026/10/18",
        "【结构化记忆】",
        "【检索到的记忆】",
        context_module.CITATION_RULE,
    ]
    positions = [text.index(token) for token in order]
    assert positions == sorted(positions)
    assert text.rstrip().endswith(context_module.CITATION_RULE)


def test_day_marker_appears_exactly_once(parts):
    parts["add"]("喜欢美式咖啡")
    fragments = [MemoryFragment(kind=KIND_DAILY, date="2026-10-17", text="昨天聊了茶")]
    result = parts["builder"]().build("你好", fragments)
    assert result.messages[0].content.count("2026/10/18") == 1
    carriers = [
        index for index, message in enumerate(result.messages) if "2026/10/18" in message.content
    ]
    assert carriers == [0]


def test_separators_share_the_same_width(parts):
    """日期分隔线与 current 分隔线同宽（30），宽度口径统一（D5）。"""
    text = parts["builder"]().build("你好").messages[0].content
    assert len(day_line(text)) == SEPARATOR_WIDTH
    assert len(CURRENT_SEPARATOR) == SEPARATOR_WIDTH


def test_current_separator_has_no_date_text(parts):
    """两条分隔线同宽但语义独立：日期分隔线带日期，current 不带。"""
    separator = day_line(parts["builder"]().build("你好").messages[0].content)
    assert "2026/10/18" in separator
    assert "current" in CURRENT_SEPARATOR
    assert not any(char.isdigit() for char in CURRENT_SEPARATOR)
    assert CURRENT_SEPARATOR != separator


def test_empty_memory_inputs_add_no_blank_sections(parts):
    """空结构化记忆 + 无片段：只留基础提示词与日期分隔线，不留空主题或多余空行。"""
    text = parts["builder"]().build("你好").messages[0].content
    assert text.endswith(day_marker(at(0)))
    assert "\n\n\n" not in text
    assert "【结构化记忆】" not in text
    assert "【检索到的记忆】" not in text
    assert context_module.CITATION_RULE not in text


def test_base_prompt_built_by_the_service_is_used_as_is(parts):
    """chat 服务先构建系统提示词，构建器只负责拼接，不重复渲染人设。"""
    base = SystemPrompt(text="【人设】\n外部构建的基础提示词", template_digest="digest-service")
    builder = ContextBuilder(PERSONA, load_templates(), TZ, base_prompt=base, clock=parts["clock"])
    assembled = builder.build("你好")
    text = assembled.messages[0].content
    assert text.startswith(base.text)
    assert text.count("【人设】") == 1
    assert assembled.template_digest == "digest-service"
    assert text.endswith(day_marker(at(21, 30)))


def test_standalone_me_and_consecutive_roles_are_kept(parts):
    lines = (
        me(at(9), "先开口"),
        master(at(9, second=1), "一"),
        master(at(9, second=2), "二"),
        me(at(9, second=3), "三"),
        me(at(9, second=4), "四"),
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
    assert any("第0轮" in message.content for message in result.messages)
    assert any("第19轮" in message.content for message in result.messages)
    assert result.warnings == ()


def test_fragments_over_budget_are_dropped_and_counted(parts, monkeypatch):
    monkeypatch.setattr(context_module, "FRAGMENT_TOKEN_BUDGET", 40)
    fragments = [MemoryFragment(kind=KIND_DAILY, date="2026-10-18", text="字" * 20)] * 4
    result = parts["builder"]().build("你好", fragments)
    assert result.dropped_fragments > 0
    assert [warning.code for warning in result.warnings] == [WARNING_FRAGMENTS_DROPPED]


def test_only_memory_unload_is_mapped_to_a_user_notice(parts, monkeypatch):
    def explode(**kwargs):
        raise MemoryReadError("坏了")

    monkeypatch.setattr(parts["entries"], "load", explode)
    result = parts["builder"]().build("你好")
    assert visible_notices(result.warnings) == (WARNING_MEMORY_UNLOADED,)


def test_dropped_fragments_stay_warnings_without_user_notices(parts, monkeypatch):
    monkeypatch.setattr(context_module, "FRAGMENT_TOKEN_BUDGET", 40)
    fragments = [MemoryFragment(kind=KIND_DAILY, date="2026-10-18", text="字" * 20)] * 4
    result = parts["builder"]().build("你好", fragments)
    assert [warning.code for warning in result.warnings] == [WARNING_FRAGMENTS_DROPPED]
    assert visible_notices(result.warnings) == ()


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
    parts["add"]("喜欢茶")
    result = parts["builder"]().build("你好")
    assert result.injected == ()
    assert "【结构化记忆】" in result.messages[0].content
    assert context_module.CITATION_RULE not in result.messages[0].content


def test_citation_rule_is_omitted_when_nothing_is_injected(parts):
    result = parts["builder"]().build("你好")
    assert result.injected == ()
    assert context_module.CITATION_RULE not in result.messages[0].content


def test_perception_events_are_standalone_assistant_messages_in_current(parts):
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
    assert messages[4].content == "[event][09:30:00]使用 code 已 5 分钟"
    assert messages[5].content == "[event][09:40:00]离开 12 分钟后回来"


def test_perception_events_precede_the_input_even_when_they_happen_later(parts):
    """current 区顺序固定：感知事件在前，本轮输入最后（D-12）。"""
    perception = FakePerception([pe(PerceptionEventKind.APP_IN_USE, 21, 40, exe="code", minutes=5)])
    messages = parts["builder"](perception=perception).build("你好").messages
    assert messages[-2].content == "[event][21:40:00]使用 code 已 5 分钟"
    assert messages[-1].content == "[master][21:30:00]你好"


def test_perception_injection_is_capped_at_five(parts):
    perception = FakePerception(
        [
            pe(PerceptionEventKind.APP_IN_USE, 8, 0, exe="a", minutes=3),
            pe(PerceptionEventKind.APP_IN_USE, 8, 10, exe="b", minutes=3),
            pe(PerceptionEventKind.APP_IN_USE, 8, 20, exe="c", minutes=3),
            pe(PerceptionEventKind.APP_IN_USE, 8, 30, exe="d", minutes=3),
            pe(PerceptionEventKind.APP_IN_USE, 8, 40, exe="e", minutes=3),
            pe(PerceptionEventKind.APP_IN_USE, 8, 50, exe="f", minutes=3),
        ]
    )
    messages = parts["builder"](perception=perception).build("你好").messages
    events = [message.content for message in messages if "[event]" in message.content]
    assert len(events) == 5
    assert "使用 b" in events[0]
    assert "使用 f" in events[-1]


def test_perception_read_failure_is_silent(parts, monkeypatch):
    perception = FakePerception([])

    def explode(limit: int) -> tuple[PerceptionEvent, ...]:
        raise OSError("采集器不可用")

    monkeypatch.setattr(perception, "recent_context_events", explode)
    result = parts["builder"](perception=perception).build("你好")
    assert not any("[event]" in message.content for message in result.messages)
    assert all(warning.code != WARNING_MEMORY_UNLOADED for warning in result.warnings)


def test_previous_turn_events_are_not_carried_into_this_turn(parts):
    """感知只临时注入本轮，下一回合不会自动带入（D-04）。"""
    first = parts["builder"](
        perception=FakePerception(
            [pe(PerceptionEventKind.APP_IN_USE, 9, 30, exe="code", minutes=5)]
        )
    ).build("你好")
    assert any("[event]" in message.content for message in first.messages)

    second = parts["builder"](perception=FakePerception([])).build("再见")
    assert not any("[event]" in message.content for message in second.messages)


def test_history_without_reply_still_inserts_current_separator(parts):
    """历史非空即插分隔线，即使还没有助手回复（D-18）。"""
    lines = (master(at(9), "一"),)
    perception = FakePerception([pe(PerceptionEventKind.APP_IN_USE, 9, 30, exe="code", minutes=5)])
    messages = (
        parts["builder"](transcript_=parts["transcript"](*lines), perception=perception)
        .build("你好")
        .messages
    )
    assert messages[1].content == expects(at(9), "master", "一")
    assert messages[2].content == CURRENT_SEPARATOR
    assert messages[3].content.startswith("[event]")


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
    assert not any("[event]" in message.content for message in messages)


def test_current_record_is_excluded_by_identity_not_by_text(parts):
    """历史里同文的另一条记录不能被误删，本轮记录按身份剔除（T-03）。"""
    lines = (
        master(at(9), "在吗", identity=1),
        me(at(9, 1), "在的", identity=2),
        master(at(10), "在吗", identity=3),
    )
    current = record(at(10), "在吗", identity=4)
    messages = (
        parts["builder"](transcript_=parts["transcript"](*lines))
        .build("在吗", current_record=current)
        .messages
    )
    # 第一条「在吗」保留在历史；本轮记录由读回内容作为最后一条输入。
    assert messages[1].content == "[master][09:00:00]在吗"
    assert messages[-1].content == "[master][10:00:00]在吗"
    assert [message.content for message in messages].count("[master][10:00:00]在吗") == 1


def test_current_record_is_not_readded_from_raw_input(parts):
    """本轮输入使用读回记录，不从原始入参再追加一份（D-03）。"""
    current = record(at(21, 30), "  你好  ", identity=7)
    messages = parts["builder"]().build("  你好  ", current_record=current).messages
    assert messages[-1].content == "[master][21:30:00]  你好  "
    assert len(messages) == 2


def test_adjacent_identical_query_is_dropped_only_for_model_context(parts):
    lines = (
        master(at(9), "在吗", identity=1),
        me(at(9, 1), "在的", identity=2),
        master(at(10), "在吗", identity=3),
        me(at(10, 1), "又在？", identity=4),
    )
    current = record(at(11), "在吗", identity=5)
    messages = (
        parts["builder"](transcript_=parts["transcript"](*lines))
        .build("在吗", current_record=current)
        .messages
    )
    assert "[master][10:00:00]在吗" not in [message.content for message in messages]
    assert "[master][09:00:00]在吗" in [message.content for message in messages]
    assert messages[-1].content == "[master][11:00:00]在吗"
    assert "[me][10:01:00]又在？" in [message.content for message in messages]


def test_a_b_a_keeps_both_queries(parts):
    lines = (
        master(at(9), "A", identity=1),
        me(at(9, 1), "答一", identity=2),
        master(at(10), "B", identity=3),
        me(at(10, 1), "答二", identity=4),
    )
    current = record(at(11), "A", identity=5)
    messages = (
        parts["builder"](transcript_=parts["transcript"](*lines))
        .build("A", current_record=current)
        .messages
    )
    contents = [message.content for message in messages if message.content.endswith("A")]
    assert contents == [
        "[master][09:00:00]A",
        "[master][11:00:00]A",
    ]


def test_invisible_current_record_does_not_delete_the_previous_query(parts):
    """本轮记录读不回来时，不能因正文相同误删上一轮（T-04）。"""
    lines = (
        master(at(9), "在吗", identity=1),
        me(at(9, 1), "在的", identity=2),
    )
    messages = (
        parts["builder"](transcript_=parts["transcript"](*lines))
        .build("在吗", current_record=None)
        .messages
    )
    assert "[master][09:00:00]在吗" in [message.content for message in messages]
