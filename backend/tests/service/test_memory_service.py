from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from module.memory.contracts import DailyLogEntry, DailyLogRole
from module.memory.memory_service import (
    MemoryReadError,
    MemoryWriteError,
    compile_pattern,
    number_lines,
    parse_ranges,
    render_daily_turns,
    section,
    split_lines,
)


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


def test_split_lines_normalizes_newlines_and_ignores_blank_text():
    assert split_lines("a\r\nb\r\n") == ["a", "b"]
    assert split_lines("  \n ") == []


def test_compile_pattern_rejects_blank_empty_match_and_bad_regex():
    with pytest.raises(MemoryWriteError, match="正则不能为空"):
        compile_pattern("  ")
    with pytest.raises(MemoryWriteError, match="空字符串"):
        compile_pattern("x*")
    with pytest.raises(MemoryWriteError, match="正则不合法"):
        compile_pattern("(")


def test_compile_pattern_matches_escaped_literal_text():
    regex = compile_pattern(r"C:\\new")
    assert regex.search(r"C:\new") is not None


def record(role: DailyLogRole, content: str) -> DailyLogEntry:
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    return DailyLogEntry(
        event_time=moment,
        role=role,
        content=content,
        created_at=moment,
    )


def test_daily_turns_are_rendered_one_line_each():
    records = [
        record("master", "喜欢美式咖啡"),
        record("me", "记住啦"),
        record("master", "第一行\n第二行"),
        record("me", "好的"),
    ]
    assert render_daily_turns(records) == [
        "用户：喜欢美式咖啡 / 宠物：记住啦",
        "用户：第一行 第二行 / 宠物：好的",
    ]


def test_orphan_records_still_get_a_line():
    records = [record("me", "开场白"), record("master", "你好")]
    assert render_daily_turns(records) == ["宠物：开场白", "用户：你好"]


def test_events_are_rendered_as_text_and_kept_in_memory():
    from module.memory.daily_events import encode_event

    records = [
        record("master", "你好"),
        record("event", encode_event("session_boundary", persona="小桌")),
        record("event", encode_event("dispatch_failed", topic="回忆", reason="超时")),
    ]
    assert render_daily_turns(records) == [
        "用户：你好 / 事件：切换人设为小桌，新会话开始 / 事件：派发失败：回忆，超时"
    ]
