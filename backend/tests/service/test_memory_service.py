from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from module.memory.contracts import DailyLogEntry, DailyLogRole
from module.memory.memory_service import (
    MemoryReader,
    MemoryReadError,
    MemoryWriteError,
    StructuredMemoryDoc,
    render_daily_turns,
)


@pytest.fixture
def doc(tmp_path: Path) -> StructuredMemoryDoc:
    return StructuredMemoryDoc(tmp_path / "memory" / "structured.md")


def test_missing_file_is_created_empty(doc: StructuredMemoryDoc):
    assert not doc.path.exists()
    assert doc.text() == ""
    assert doc.path.read_text(encoding="utf-8") == ""
    assert doc.lines() == []


def test_lines_split_document_and_ignore_trailing_newline(doc: StructuredMemoryDoc):
    doc.save("a\nb\nc\n")
    assert doc.lines() == ["a", "b", "c"]
    assert doc.line_count() == 3


def test_save_normalizes_newlines_and_keeps_single_trailing_newline(doc: StructuredMemoryDoc):
    doc.save("## 主人信息\r\n名字：小李\r\n")
    assert doc.text() == "## 主人信息\n名字：小李\n"


def test_save_empty_text_clears_document(doc: StructuredMemoryDoc):
    doc.save("   ")
    assert doc.text() == ""
    assert doc.lines() == []


def test_save_writes_atomically_without_leftovers(doc: StructuredMemoryDoc):
    doc.save("## 主人信息\n")
    doc.save("## 喜好\n")
    assert list(doc.path.parent.glob("*.tmp")) == []


def test_listeners_are_notified_on_every_write(doc: StructuredMemoryDoc):
    doc.save("a\nb")
    calls: list[int] = []
    doc.add_listener(lambda: calls.append(1))
    doc.append("咖啡偏好")
    doc.replace_pattern([[3, 3]], "咖啡偏好", "喜欢美式")
    doc.save("")
    assert len(calls) == 3


def test_append_returns_written_line_numbers(doc: StructuredMemoryDoc):
    doc.save("a")
    start, end = doc.append("咖啡偏好\n喜欢美式")
    assert (start, end) == (2, 3)
    assert doc.lines() == ["a", "咖啡偏好", "喜欢美式"]


def test_append_rejects_blank_content(doc: StructuredMemoryDoc):
    with pytest.raises(MemoryWriteError, match="内容不能为空"):
        doc.append("\n \n")


def test_replace_pattern_edits_only_matched_text(doc: StructuredMemoryDoc):
    doc.save("名字：小李\n工作地点：杭州")
    assert doc.replace_pattern([[2, 2]], "杭州", "上海") == (2, 2)
    assert doc.lines() == ["名字：小李", "工作地点：上海"]


def test_replace_pattern_accepts_multiple_ranges(doc: StructuredMemoryDoc):
    doc.save("a\ncity = 杭州\nc\nd\ncity = 杭州")
    assert doc.replace_pattern([[5, 5], [2, 2]], "杭州", "上海") == (2, 5)
    assert doc.lines() == ["a", "city = 上海", "c", "d", "city = 上海"]


def test_replace_pattern_keeps_unmatched_ranges_untouched(doc: StructuredMemoryDoc):
    doc.save("a\nb\nc")
    assert doc.replace_pattern([[1, 1], [3, 3]], "c", "C") == (3, 3)
    assert doc.lines() == ["a", "b", "C"]


def test_replace_pattern_without_match_is_rejected(doc: StructuredMemoryDoc):
    doc.save("a\nb")
    with pytest.raises(MemoryWriteError, match="没有匹配到内容"):
        doc.replace_pattern([[1, 2]], "杭州", "上海")
    assert doc.lines() == ["a", "b"]


def test_replace_pattern_rejects_bad_pattern(doc: StructuredMemoryDoc):
    doc.save("a")
    with pytest.raises(MemoryWriteError, match="正则不合法"):
        doc.replace_pattern([[1, 1]], "(", "B")


@pytest.mark.parametrize("pattern", ["", " ", ".*"])
def test_replace_pattern_rejects_empty_match(doc: StructuredMemoryDoc, pattern: str):
    doc.save("a")
    with pytest.raises(MemoryWriteError):
        doc.replace_pattern([[1, 1]], pattern, "B")


def test_replace_pattern_treats_content_as_literal_text(doc: StructuredMemoryDoc):
    doc.save("路径：old")
    doc.replace_pattern([[1, 1]], "old", r"C:\new")
    assert doc.lines() == ["路径：C:\\new"]


@pytest.mark.parametrize("spec", [[[0, 1]], [[2, 1]], [[1, 9]], [[1, 2, 3]], []])
def test_replace_pattern_rejects_bad_ranges(doc: StructuredMemoryDoc, spec: list):
    doc.save("a\nb")
    with pytest.raises(MemoryWriteError):
        doc.replace_pattern(spec, "a", "B")
    assert doc.lines() == ["a", "b"]


def test_replace_pattern_rejects_overlapping_ranges(doc: StructuredMemoryDoc):
    doc.save("a\nb\nc")
    with pytest.raises(MemoryWriteError, match="重叠"):
        doc.replace_pattern([[1, 2], [2, 3]], "a", "B")


def test_read_errors_become_write_errors(doc: StructuredMemoryDoc):
    doc.save("a")
    with pytest.raises(MemoryWriteError):
        doc.replace_pattern([[1, 2]], "a", "B")


class FakeDaily:
    def read_day(self, day: str) -> list:
        return []


def test_reader_reports_missing_index(doc: StructuredMemoryDoc, tmp_path: Path):
    reader = MemoryReader(tmp_path / "missing.json", doc, FakeDaily())
    with pytest.raises(MemoryReadError, match="记忆索引不存在"):
        reader.describe()


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
