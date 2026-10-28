from pathlib import Path

import pytest

from infra.store.repositories.daily_log import DailyLogRecord
from service.memory.memory_service import (
    DEFAULT_STRUCTURED_DOC,
    MemoryReader,
    MemoryReadError,
    MemoryWriteError,
    StructuredMemoryDoc,
    render_daily_turns,
)

TEMPLATE_LINES = DEFAULT_STRUCTURED_DOC.rstrip("\n").split("\n")


@pytest.fixture
def doc(tmp_path: Path) -> StructuredMemoryDoc:
    return StructuredMemoryDoc(tmp_path / "memory" / "structured.md")


def test_missing_file_is_created_from_template(doc: StructuredMemoryDoc):
    assert not doc.path.exists()
    assert doc.text() == DEFAULT_STRUCTURED_DOC
    assert doc.path.read_text(encoding="utf-8") == DEFAULT_STRUCTURED_DOC


def test_lines_split_document_and_ignore_trailing_newline(doc: StructuredMemoryDoc):
    assert doc.lines() == TEMPLATE_LINES
    assert doc.line_count() == len(TEMPLATE_LINES)


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
    doc.text()
    calls: list[int] = []
    doc.add_listener(lambda: calls.append(1))
    doc.append("咖啡偏好")
    doc.replace_lines("1", "## 喜好")
    doc.save("")
    assert len(calls) == 3


def test_append_returns_written_line_numbers(doc: StructuredMemoryDoc):
    start, end = doc.append("咖啡偏好\n喜欢美式")
    assert (start, end) == (len(TEMPLATE_LINES) + 1, len(TEMPLATE_LINES) + 2)
    assert doc.lines()[-2:] == ["咖啡偏好", "喜欢美式"]


def test_append_rejects_blank_content(doc: StructuredMemoryDoc):
    with pytest.raises(MemoryWriteError, match="内容不能为空"):
        doc.append("\n \n")


def test_replace_lines_swaps_range_in_place(doc: StructuredMemoryDoc):
    doc.save("a\nb\nc\nd")
    assert doc.replace_lines("2-3", "B") == (2, 2)
    assert doc.lines() == ["a", "B", "d"]


def test_replace_lines_accepts_single_number(doc: StructuredMemoryDoc):
    doc.save("a\nb")
    assert doc.replace_lines("2", "B") == (2, 2)
    assert doc.lines() == ["a", "B"]


@pytest.mark.parametrize("spec", ["0-1", "2-1", "1-9", "x"])
def test_replace_lines_rejects_bad_ranges(doc: StructuredMemoryDoc, spec: str):
    doc.save("a\nb")
    with pytest.raises(MemoryWriteError):
        doc.replace_lines(spec, "B")
    assert doc.lines() == ["a", "b"]


def test_read_errors_become_write_errors(doc: StructuredMemoryDoc):
    doc.save("a")
    with pytest.raises(MemoryWriteError):
        doc.replace_lines("1-2", "B")


class FakeDaily:
    def read_day(self, day: str) -> list:
        return []


def test_reader_reports_missing_index(doc: StructuredMemoryDoc, tmp_path: Path):
    reader = MemoryReader(tmp_path / "missing.json", doc, FakeDaily())
    with pytest.raises(MemoryReadError, match="记忆索引不存在"):
        reader.describe()


def record(role: str, content: str, record_id: int = 1) -> DailyLogRecord:
    return DailyLogRecord(
        id=record_id,
        date="2026-10-18",
        role=role,
        content=content,
        created_at="2026-10-18T09:00:00+08:00",
    )


def test_daily_turns_are_rendered_one_line_each():
    records = [
        record("master", "喜欢美式咖啡", 1),
        record("me", "记住啦", 2),
        record("master", "第一行\n第二行", 3),
        record("me", "好的", 4),
    ]
    assert render_daily_turns(records) == [
        "用户：喜欢美式咖啡 / 宠物：记住啦",
        "用户：第一行 第二行 / 宠物：好的",
    ]


def test_orphan_records_still_get_a_line():
    records = [record("me", "开场白", 1), record("master", "你好", 2)]
    assert render_daily_turns(records) == ["宠物：开场白", "用户：你好"]


def test_events_are_rendered_as_text_and_kept_in_memory():
    from service.memory.daily_events import encode_event

    records = [
        record("master", "你好", 1),
        record("event", encode_event("session_boundary", persona="小桌"), 2),
        record("event", encode_event("dispatch_failed", capability="记忆检索", reason="超时"), 3),
    ]
    assert render_daily_turns(records) == [
        "用户：你好 / 事件：切换人设为小桌，新会话开始 / 事件：派发失败：记忆检索，超时"
    ]
