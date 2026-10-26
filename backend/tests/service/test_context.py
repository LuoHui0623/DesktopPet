from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from config.persona import PersonaConfig
from infra.files.json_store import JsonDirectory
from infra.store.app_settings import DbAppSettings
from infra.store.core_memory import DbCoreMemory
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.prompt import load_templates
from service.chat import context as context_module
from service.chat.context import KIND_DAILY, KIND_STRUCTURED, ContextBuilder, MemoryFragment
from service.memory.core_memory import CoreMemoryService
from service.memory.daily_log_service import DailyLogService
from service.memory.memory_service import MemorySource, StructuredMemoryService

TZ = ZoneInfo("Asia/Shanghai")
SOURCE = MemorySource(type="keyword", date="2026-10-18")
PERSONA = PersonaConfig(content={"role": {"name": "小桌", "personality": ("温和",)}})


class DictSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


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
    structured = StructuredMemoryService(
        JsonDirectory(tmp_path / "memory" / "structured"),
        DictSettings(),
        TZ,
        clock=clock,
    )
    daily = DailyLogService(DbDailyLog(db_file), DbAppSettings(db_file), TZ)
    core = CoreMemoryService(DbCoreMemory(db_file), structured)

    def builder() -> ContextBuilder:
        return ContextBuilder(
            PERSONA,
            load_templates(),
            daily,
            TZ,
            core=core,
            structured=structured,
            clock=clock,
        )

    return {
        "db_file": db_file,
        "clock": clock,
        "structured": structured,
        "daily": daily,
        "core": core,
        "builder": builder,
    }


def add_memory(structured: StructuredMemoryService, content: str, category="preference"):
    return structured.create(category=category, title=content[:4], content=content, source=SOURCE)


def test_persona_is_first_then_core_then_input(parts):
    add_memory(parts["structured"], "喜欢美式咖啡，不加糖")
    parts["core"].add(parts["structured"].list().items[0].id)
    result = parts["builder"]().build("你好")
    system = result.messages[0]
    assert isinstance(system, SystemMessage)
    assert system.content.index("【人设】") < system.content.index("【核心典藏】")
    assert result.messages[-1] == HumanMessage(content="你好")


def test_structured_tag_matches_the_documented_example(parts):
    memory = add_memory(parts["structured"], "喜欢美式咖啡，不加糖")
    parts["core"].add(memory.id)
    text = parts["builder"]().build("你好").messages[0].content
    assert "[结构化记忆 · 偏好 · 2026-10-18] 喜欢美式咖啡，不加糖" in text


def test_core_block_follows_core_order_and_reorder_changes_it(parts):
    first = add_memory(parts["structured"], "第一条偏好").id
    second = add_memory(parts["structured"], "第二条偏好").id
    parts["core"].add(first)
    parts["core"].add(second)
    before = parts["builder"]().build("你好").messages[0].content
    assert before.index("第一条偏好") < before.index("第二条偏好")

    parts["core"].reorder([second, first])
    after = parts["builder"]().build("你好").messages[0].content
    assert after.index("第二条偏好") < after.index("第一条偏好")


def test_retrieved_fragments_are_labelled(parts):
    fragments = [
        MemoryFragment(kind=KIND_DAILY, date="2026-10-18", text="早安\n你好"),
        MemoryFragment(
            kind=KIND_STRUCTURED,
            date="2026-10-18",
            text="喜欢茶",
            category="preference",
            memory_id="m_20261018_001",
        ),
    ]
    text = parts["builder"]().build("你好", fragments).messages[0].content
    assert "【检索到的记忆】" in text
    assert "[每日对话 · 2026-10-18] 早安 你好" in text
    assert "[结构化记忆 · 偏好 · 2026-10-18] 喜欢茶" in text


def test_recent_turns_follow_the_day_in_order(parts):
    store = DbDailyLog(parts["db_file"])
    store.append(
        "2026-10-18",
        "2026-10-18T09:00:00+08:00",
        [("user", "一"), ("assistant", "二")],
    )
    store.append("2026-10-18", "2026-10-18T10:00:00+08:00", [("user", "三"), ("assistant", "四")])
    messages = parts["builder"]().build("五").messages
    assert [type(message) for message in messages[1:-1]] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
        AIMessage,
    ]
    assert [message.content for message in messages[1:-1]] == ["一", "二", "三", "四"]


def test_new_day_starts_a_new_session(parts):
    DbDailyLog(parts["db_file"]).append(
        "2026-10-18",
        "2026-10-18T23:00:00+08:00",
        [("user", "昨天的话"), ("assistant", "好")],
    )
    parts["clock"].moment = datetime(2026, 10, 19, 8, 0, tzinfo=TZ)
    messages = parts["builder"]().build("早").messages
    assert [message.content for message in messages[1:-1]] == []


def test_over_budget_trims_oldest_turns_and_keeps_persona_and_core(parts, monkeypatch):
    memory = add_memory(parts["structured"], "核心偏好内容")
    parts["core"].add(memory.id)
    store = DbDailyLog(parts["db_file"])
    for index in range(6):
        store.append(
            "2026-10-18",
            f"2026-10-18T09:0{index}:00+08:00",
            [("user", f"第{index}轮" + "问" * 80), ("assistant", "答" * 80)],
        )
    monkeypatch.setattr(context_module, "CONTEXT_TOKEN_BUDGET", 700)
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


def test_core_memory_is_never_trimmed_even_when_over_budget(parts, monkeypatch):
    memory = add_memory(parts["structured"], "必须保留的核心记忆")
    parts["core"].add(memory.id)
    monkeypatch.setattr(context_module, "CONTEXT_TOKEN_BUDGET", 50)
    result = parts["builder"]().build("你好")
    assert "必须保留的核心记忆" in result.messages[0].content
    assert any("固定部分超出预算" in warning for warning in result.warnings)


def test_fragments_over_budget_are_dropped_and_counted(parts, monkeypatch):
    monkeypatch.setattr(context_module, "FRAGMENT_TOKEN_BUDGET", 40)
    fragments = [MemoryFragment(kind=KIND_DAILY, date="2026-10-18", text="字" * 20)] * 4
    result = parts["builder"]().build("你好", fragments)
    assert result.dropped_fragments > 0
    assert any("已舍弃" in warning for warning in result.warnings)


def test_unreadable_core_entry_is_skipped_with_warning(parts):
    memory = add_memory(parts["structured"], "会被损坏的记忆")
    parts["core"].add(memory.id)
    (parts["structured"]._files.directory / f"{memory.id}.json").write_text("{", encoding="utf-8")
    result = parts["builder"]().build("你好")
    assert "会被损坏的记忆" not in result.messages[0].content
    assert any(memory.id in warning for warning in result.warnings)


def test_result_reports_template_digest_and_size(parts):
    result = parts["builder"]().build("你好")
    assert len(result.template_digest) == 64
    assert result.chars == sum(len(message.content) for message in result.messages)


def test_injected_lists_core_and_kept_fragments_only(parts):
    memory = add_memory(parts["structured"], "喜欢美式咖啡，不加糖")
    parts["core"].add(memory.id)
    fragments = [
        MemoryFragment(kind=KIND_DAILY, date="2026-10-18", text="字" * 20),
        MemoryFragment(kind=KIND_DAILY, date="2026-10-17", text="字" * 2000),
    ]
    result = parts["builder"]().build("你好", fragments)
    assert [fragment.memory_id or fragment.date for fragment in result.injected] == [
        memory.id,
        "2026-10-18",
    ]
    assert result.dropped_fragments == 1
    system = result.messages[0].content
    assert f"（id：{memory.id}）" in system
    assert context_module.CITATION_RULE in system


def test_citation_rule_is_omitted_when_nothing_is_injected(parts):
    result = parts["builder"]().build("你好")
    assert result.injected == ()
    assert context_module.CITATION_RULE not in result.messages[0].content
