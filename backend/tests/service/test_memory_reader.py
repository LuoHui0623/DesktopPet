from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.skills import SkillRegistry, memory_retrieve_skill, memory_write_skill
from module.agent.tools.explore import make_explore_tools
from module.memory.contracts import DailyLogRecord, DailyLogRole
from module.memory.daily_log_service import DailyLogService
from module.memory.index import MemoryIndexService
from module.memory.memory_service import (
    MemoryReader,
    MemoryReadError,
    StructuredMemoryDoc,
    number_lines,
    parse_ranges,
    section,
)
from module.memory.repository import DbDailyLog
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")


def rec(day: str, created_at: str, role: DailyLogRole, content: str) -> DailyLogRecord:
    return DailyLogRecord(date=day, role=role, content=content, created_at=created_at)


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
    daily = DailyLogService(DbDailyLog(db_file), SettingsService(db_file), TZ)
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


def test_parse_ranges_accepts_single_and_multiple_specs():
    assert parse_ranges([[2, 5]]) == [(2, 5)]
    assert parse_ranges([[5]]) == [(5, 5)]
    assert parse_ranges([[9, 10], [1, 2]]) == [(1, 2), (9, 10)]


@pytest.mark.parametrize("spec", [[[0, 3]], [[4, 2]], [[1, 2, 3]], [[1, 2], [2, 4]], []])
def test_parse_ranges_rejects_bad_specs(spec: list):
    with pytest.raises(MemoryReadError):
        parse_ranges(spec)


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
    assert section(lines, [[2, 3]]) == "2: b\n3: c"


def test_section_keeps_real_line_numbers_across_ranges():
    lines = ["a", "b", "c", "d"]
    assert section(lines, [[1, 2], [4, 4]]) == "1: a\n2: b\n4: d"


def test_section_out_of_range_returns_reason():
    with pytest.raises(MemoryReadError, match="超出范围，共 3 行"):
        section(["a", "b", "c"], [[2, 4]])


def test_describe_and_index_come_from_index_file(parts):
    reader: MemoryReader = parts["reader"]
    assert set(reader.describe()) == {"structured", "daily"}
    assert reader.index()["structured"]["lines"] == 0


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
    parts["structured"].save("## 主人信息\n名字：小李\n## 喜好\n喜欢美式\n")
    assert parts["reader"].read("structured", "structured") == number_lines(
        ["## 主人信息", "名字：小李", "## 喜好", "喜欢美式"]
    )
    assert (
        parts["reader"].read("structured", "structured.md", [[1], [3]])
        == "1: ## 主人信息\n3: ## 喜好"
    )


def test_reads_structured_doc_after_append(parts):
    start, end = parts["structured"].append("咖啡偏好\n喜欢美式咖啡")
    assert (start, end) == (1, 2)
    assert parts["reader"].read("structured", "structured", [[start, end]]) == (
        f"{start}: 咖啡偏好\n{end}: 喜欢美式咖啡"
    )


def test_reads_daily_turns_one_line_per_turn(parts):
    store = DbDailyLog(parts["db_file"])
    store.append(
        [
            rec("2026-10-18", "2026-10-18T09:00:00+08:00", "master", "喜欢美式咖啡"),
            rec("2026-10-18", "2026-10-18T09:00:00+08:00", "me", "记住啦"),
        ]
    )
    store.append([rec("2026-10-18", "2026-10-18T10:00:00+08:00", "master", "早安")])
    text = parts["reader"].read("daily", "2026-10-18")
    assert text == "1: 用户：喜欢美式咖啡 / 宠物：记住啦\n2: 用户：早安"
    assert parts["reader"].read("daily", "2026-10-18", [[2]]) == "2: 用户：早安"


def test_daily_range_out_of_bounds_is_reported(parts):
    DbDailyLog(parts["db_file"]).append(
        [rec("2026-10-18", "2026-10-18T09:00:00+08:00", "master", "早")]
    )
    with pytest.raises(MemoryReadError, match="超出范围"):
        parts["reader"].read("daily", "2026-10-18", [[1, 3]])


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
    assert read["content"] == "1: 喜欢美式咖啡"


def test_tool_failures_return_readable_reasons(tools):
    result = tools["read"].invoke({"resource": "memory", "name": "2026-10-01"})
    assert result["ok"] is False
    assert result["error"]
    assert result["content"] is None


def test_tool_range_error_is_returned_not_raised(parts, tools):
    DbDailyLog(parts["db_file"]).append(
        [rec("2026-10-18", "2026-10-18T09:00:00+08:00", "master", "早")]
    )
    result = tools["read"].invoke({"resource": "memory", "name": "2026-10-18", "lines": [[1, 9]]})
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
