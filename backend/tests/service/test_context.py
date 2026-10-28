from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from config.persona import parse_persona
from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.prompt import load_templates
from service.chat import context as context_module
from service.chat.context import KIND_DAILY, ContextBuilder, MemoryFragment
from service.memory.daily_log_service import DailyLogService
from service.memory.memory_service import MemoryReadError, StructuredMemoryDoc

TZ = ZoneInfo("Asia/Shanghai")
PERSONA = parse_persona({"role": {"名字": "小桌", "人设": "陪伴"}, "personality": ["温和"]})


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


@pytest.fixture
def parts(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    clock = Clock(datetime(2026, 10, 18, 21, 30, tzinfo=TZ))
    structured = StructuredMemoryDoc(tmp_path / "memory" / "structured.md")
    daily = DailyLogService(DbDailyLog(db_file), DbAppSettings(db_file), TZ)

    def builder() -> ContextBuilder:
        return ContextBuilder(
            PERSONA, load_templates(), daily, TZ, structured=structured, clock=clock
        )

    return {
        "db_file": db_file,
        "clock": clock,
        "structured": structured,
        "daily": daily,
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


def test_recent_turns_follow_the_day_in_order(parts):
    store = DbDailyLog(parts["db_file"])
    store.append(
        "2026-10-18",
        "2026-10-18T09:00:00+08:00",
        [("master", "一"), ("me", "二")],
    )
    store.append("2026-10-18", "2026-10-18T10:00:00+08:00", [("master", "三"), ("me", "四")])
    messages = parts["builder"]().build("五").messages
    assert [type(message) for message in messages[1:-1]] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
        AIMessage,
    ]
    assert [message.content for message in messages[1:-1]] == [
        "一",
        '{"type": "chat", "text": "二"}',
        "三",
        '{"type": "chat", "text": "四"}',
    ]


def test_events_follow_their_type_in_recent_turns(parts):
    from service.memory.daily_events import encode_event

    DbDailyLog(parts["db_file"]).append(
        "2026-10-18",
        "2026-10-18T09:00:00+08:00",
        [
            ("master", "一"),
            ("me", "二"),
            ("event", encode_event("session_boundary", persona="小桌")),
            ("event", encode_event("dispatch_failed", capability="记忆检索", reason="超时")),
            ("master", "三"),
        ],
    )
    messages = parts["builder"]().build("你好").messages
    assert [type(message) for message in messages[1:-1]] == [
        HumanMessage,
        AIMessage,
        SystemMessage,
        HumanMessage,
    ]
    assert messages[3].content == "[事件] 派发失败：记忆检索，超时"


def test_event_before_any_turn_is_kept_as_its_own_entry(parts):
    from service.memory.daily_events import encode_event

    DbDailyLog(parts["db_file"]).append(
        "2026-10-18",
        "2026-10-18T09:00:00+08:00",
        [("event", encode_event("approval_rejected", capability="记忆写入", reason="未确认"))],
    )
    messages = parts["builder"]().build("你好").messages
    assert [message.content for message in messages[1:-1]] == [
        "[事件] 审批被拒绝：记忆写入，未确认"
    ]


def test_new_day_starts_a_new_session(parts):
    DbDailyLog(parts["db_file"]).append(
        "2026-10-18",
        "2026-10-18T23:00:00+08:00",
        [("master", "昨天的话"), ("me", "好")],
    )
    parts["clock"].moment = datetime(2026, 10, 19, 8, 0, tzinfo=TZ)
    messages = parts["builder"]().build("早").messages
    assert [message.content for message in messages[1:-1]] == []


def test_over_budget_trims_oldest_turns_and_keeps_persona_and_structured(parts, monkeypatch):
    parts["structured"].save("## 喜好\n核心偏好内容")
    store = DbDailyLog(parts["db_file"])
    for index in range(6):
        store.append(
            "2026-10-18",
            f"2026-10-18T09:0{index}:00+08:00",
            [("master", f"第{index}轮" + "问" * 80), ("me", "答" * 80)],
        )
    monkeypatch.setattr(context_module, "CONTEXT_TOKEN_BUDGET", 100_000)
    fixed = len(parts["builder"]().build("现在").messages[0].content) + len("现在")
    monkeypatch.setattr(context_module, "CONTEXT_TOKEN_BUDGET", fixed + 400)
    result = parts["builder"]().build("现在")
    system = result.messages[0].content
    assert "【人设】" in system
    assert "核心偏好内容" in system
    assert result.trimmed_turns > 0
    kept = [
        message.content for message in result.messages[1:-1] if isinstance(message, HumanMessage)
    ]
    assert kept[-1].startswith("第5轮")
    assert not any(text.startswith("第0轮") for text in kept)
    assert any("近期对话超出预算" in warning for warning in result.warnings)


def test_persona_and_structured_are_kept_when_fixed_part_over_budget(parts, monkeypatch):
    parts["structured"].save("## 喜好\n必须保留的结构化记忆")
    monkeypatch.setattr(context_module, "CONTEXT_TOKEN_BUDGET", 50)
    result = parts["builder"]().build("你好")
    assert "必须保留的结构化记忆" in result.messages[0].content
    assert any("固定部分超出预算" in warning for warning in result.warnings)


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
