from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.agent.skills import SkillRegistry, memory_retrieve_skill, memory_write_skill
from service.agent.tools.explore import make_explore_tools
from service.memory.daily_log_service import DailyLogService
from service.memory.index import MemoryIndexService
from service.memory.memory_service import (
    DEFAULT_STRUCTURED_DOC,
    MemoryReader,
    MemoryReadError,
    StructuredMemoryDoc,
    number_lines,
    parse_range,
    section,
)

TZ = ZoneInfo("Asia/Shanghai")
TEMPLATE_LINES = DEFAULT_STRUCTURED_DOC.rstrip("\n").split("\n")


class Clock:
    def __call__(self, zone: ZoneInfo) -> datetime:
        return datetime(2026, 10, 18, 21, 30, tzinfo=TZ).astimezone(zone)


@pytest.fixture
def parts(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    structured = StructuredMemoryDoc(tmp_path / "memory" / "structured.md")
    daily = DailyLogService(DbDailyLog(db_file), DbAppSettings(db_file), TZ)
    index_file = tmp_path / "memory" / "index.json"
    index = MemoryIndexService(structured.lines, daily.dates, index_file)
    structured.add_listener(index.rebuild)
    index.rebuild()
    return {
        "structured": structured,
        "daily": daily,
        "index_file": index_file,
        "index": index,
        "db_file": db_file,
        "reader": MemoryReader(index_file, structured, daily),
    }


def test_parse_range_accepts_from_to():
    assert parse_range("2-5") == (2, 5)
    assert parse_range(" 1 - 1 ") == (1, 1)
    assert parse_range("5") == (5, 5)


@pytest.mark.parametrize("spec", ["a-b", "0-3", "4-2", "1-2-3"])
def test_parse_range_rejects_bad_specs(spec: str):
    with pytest.raises(MemoryReadError):
        parse_range(spec)


def test_line_numbers_start_at_one_and_do_not_change_content():
    original = ["第一行", "", "第三行 with 符号 :", "  缩进"]
    numbered = number_lines(original)
    assert numbered.split("\n") == [
        "1: 第一行",
        "2: ",
        "3: 第三行 with 符号 :",
        "4:   缩进",
    ]
    stripped = [line.split(": ", 1)[1] if ": " in line else "" for line in numbered.split("\n")]
    assert stripped == original


def test_section_returns_numbered_slice():
    lines = ["a", "b", "c", "d"]
    assert section(lines, "2-3") == "2: b\n3: c"


def test_section_out_of_range_returns_reason():
    with pytest.raises(MemoryReadError, match="超出范围，共 3 行"):
        section(["a", "b", "c"], "2-4")


def test_describe_and_index_come_from_index_file(parts):
    reader: MemoryReader = parts["reader"]
    assert set(reader.describe()) == {"structured", "daily"}
    assert reader.index()["structured"]["lines"] == len(TEMPLATE_LINES)


def test_structured_index_lists_headings(parts):
    parts["structured"].save("## 主人信息\n名字：小李\n## 喜好\n喜欢美式\n")
    parts["index"].rebuild()
    indexed = parts["reader"].index()["structured"]
    assert indexed["name"] == "structured"
    assert indexed["lines"] == 4
    assert [item["line"] for item in indexed["headings"]] == [1, 3]


def test_missing_index_is_reported(parts):
    reader = MemoryReader(
        parts["index_file"].with_name("missing.json"), parts["structured"], parts["daily"]
    )
    with pytest.raises(MemoryReadError, match="记忆索引不存在"):
        reader.describe()


def test_reads_structured_doc_with_line_numbers(parts):
    assert parts["reader"].read("structured", "structured") == number_lines(TEMPLATE_LINES)
    assert (
        parts["reader"].read("structured", "structured.md", "1-2") == "1: ## 主人信息\n2: ## 喜好"
    )


def test_reads_structured_doc_after_append(parts):
    start, end = parts["structured"].append("咖啡偏好\n喜欢美式咖啡")
    assert (start, end) == (len(TEMPLATE_LINES) + 1, len(TEMPLATE_LINES) + 2)
    assert parts["reader"].read("structured", "structured", f"{start}-{end}") == (
        f"{start}: 咖啡偏好\n{end}: 喜欢美式咖啡"
    )


def test_reads_daily_turns_one_line_per_turn(parts):
    store = DbDailyLog(parts["db_file"])
    store.append(
        "2026-10-18",
        "2026-10-18T09:00:00+08:00",
        [("master", "喜欢美式咖啡"), ("me", "记住啦")],
    )
    store.append("2026-10-18", "2026-10-18T10:00:00+08:00", [("master", "早安")])
    text = parts["reader"].read("daily", "2026-10-18")
    assert text == "1: 用户：喜欢美式咖啡 / 宠物：记住啦\n2: 用户：早安"
    assert parts["reader"].read("daily", "2026-10-18", "2-2") == "2: 用户：早安"


def test_daily_range_out_of_bounds_is_reported(parts):
    DbDailyLog(parts["db_file"]).append(
        "2026-10-18", "2026-10-18T09:00:00+08:00", [("master", "早")]
    )
    with pytest.raises(MemoryReadError, match="超出范围"):
        parts["reader"].read("daily", "2026-10-18", "1-3")


def test_missing_entries_are_reported(parts):
    with pytest.raises(MemoryReadError, match="没有对话记录"):
        parts["reader"].read("daily", "2026-10-01")


def test_bad_date_and_unknown_kind_are_reported(parts):
    with pytest.raises(MemoryReadError, match="日期格式"):
        parts["reader"].read("daily", "2026-13-01")
    with pytest.raises(MemoryReadError, match="未知的记忆类型"):
        parts["reader"].read("secret", "x")


@pytest.fixture
def skills() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(memory_write_skill())
    registry.register(memory_retrieve_skill())
    return registry


@pytest.fixture
def tools(parts, skills):
    return {tool.name: tool for tool in make_explore_tools(parts["reader"], skills)}


def test_tools_return_structured_results(parts, tools):
    parts["structured"].append("喜欢美式咖啡")
    parts["index"].rebuild()
    indexed = tools["index"].invoke({"resource": "memory"})
    assert indexed["ok"] is True
    assert indexed["action"] == "index"
    assert "daily" in indexed["content"]
    assert "structured" in indexed["content"]
    read = tools["read"].invoke({"resource": "memory", "name": "structured"})
    assert read == {
        "ok": True,
        "action": "read",
        "content": read["content"],
        "error": None,
    }
    assert read["content"].startswith("1: ## 主人信息")
    assert "喜欢美式咖啡" in read["content"]


def test_tool_failures_return_readable_reasons(tools):
    result = tools["read"].invoke({"resource": "memory", "name": "2026-10-01"})
    assert result["ok"] is False
    assert result["error"]
    assert result["content"] is None


def test_tool_range_error_is_returned_not_raised(parts, tools):
    DbDailyLog(parts["db_file"]).append(
        "2026-10-18", "2026-10-18T09:00:00+08:00", [("master", "早")]
    )
    result = tools["read"].invoke({"resource": "memory", "name": "2026-10-18", "lines": "1-9"})
    assert result["ok"] is False
    assert "超出范围" in result["error"]


def test_tool_without_index_reports_missing_index(parts, skills):
    reader = MemoryReader(
        parts["index_file"].with_name("missing.json"), parts["structured"], parts["daily"]
    )
    tools = {tool.name: tool for tool in make_explore_tools(reader, skills)}
    result = tools["index"].invoke({"resource": "memory"})
    assert result["ok"] is False
    assert "记忆索引不存在" in result["error"]
