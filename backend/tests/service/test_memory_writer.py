import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.memory.daily_log_service import DailyLogService
from module.memory.index import MemoryIndexService
from module.memory.memory_service import (
    MemoryWriteError,
    MemoryWriter,
    StructuredMemoryDoc,
)
from module.memory.repository import DbDailyLog
from module.memory.sensitive import sensitive_reason
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def stack(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    settings = SettingsService(db_file)
    structured = StructuredMemoryDoc(tmp_path / "memory" / "structured.md")
    daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
    index_file = tmp_path / "memory" / "index.json"
    index = MemoryIndexService(structured.lines, daily.dates, index_file)
    structured.add_listener(index.rebuild)
    index.rebuild()
    writer = MemoryWriter(structured)
    return {
        "structured": structured,
        "writer": writer,
        "index_file": index_file,
        "settings": settings,
    }


def test_append_adds_lines_at_the_end(stack):
    start, end = stack["writer"].append("咖啡偏好\n喜欢美式咖啡，不加糖")
    assert (start, end) == (1, 2)
    assert stack["structured"].lines()[start - 1 : end] == ["咖啡偏好", "喜欢美式咖啡，不加糖"]


def test_append_multi_line_content_is_split_into_lines(stack):
    start, _ = stack["writer"].append("咖啡偏好\n\n喜欢美式")
    lines = stack["structured"].lines()
    assert lines[start - 1 : start + 2] == ["咖啡偏好", "", "喜欢美式"]


def test_append_rejects_blank_content(stack):
    with pytest.raises(MemoryWriteError, match="内容不能为空"):
        stack["writer"].append("   ")


def test_replace_pattern_swaps_matched_text_in_range(stack):
    start, _ = stack["writer"].append("咖啡：喜欢美式")
    replaced = stack["writer"].replace_pattern([[start]], "美式", "只喝美式")
    assert replaced == (start, start)
    assert stack["structured"].lines() == ["咖啡：喜欢只喝美式"]


def test_replace_pattern_out_of_range_is_rejected(stack):
    with pytest.raises(MemoryWriteError, match="超出范围"):
        stack["writer"].replace_pattern([[1, 99]], "x", "y")


def test_replace_pattern_requires_a_match(stack):
    stack["writer"].append("作息：晚睡")
    with pytest.raises(MemoryWriteError, match="没有匹配到内容"):
        stack["writer"].replace_pattern([[1]], "早睡", "晚睡")
    assert stack["structured"].lines() == ["作息：晚睡"]


@pytest.mark.parametrize(
    ("content", "label"),
    [
        ("我的身份证是 11010119900307123X", "证件号"),
        ("护照号 E12345678", "证件号"),
        ("卡号 6222021234567890123", "银行卡号"),
        ("网银密码是 abc", "密码"),
        ("上周刚确诊了高血压", "健康细节"),
    ],
)
def test_sensitive_information_is_rejected_with_reason(stack, content: str, label: str):
    with pytest.raises(MemoryWriteError, match=label):
        stack["writer"].append(content)
    with pytest.raises(MemoryWriteError, match=label):
        stack["writer"].replace_pattern([[1]], "密码", content)
    assert stack["structured"].lines() == []


def test_sensitive_reason_is_none_for_ordinary_text():
    assert sensitive_reason("喜欢美式咖啡，不加糖") is None
    assert sensitive_reason("我在 2026 年有 3 个会议") is None


def test_legacy_temporary_setting_does_not_block_append_or_replace(stack):
    stack["settings"].put("memory.temporary_mode", "true")
    start, _ = stack["writer"].append("咖啡偏好")
    replaced = stack["writer"].replace_pattern([[start]], "咖啡", "茶")
    assert replaced == (start, start)
    assert stack["structured"].lines() == ["茶偏好"]


def test_writes_refresh_index(stack):
    stack["writer"].append("咖啡偏好")
    index = json.loads(stack["index_file"].read_text(encoding="utf-8"))
    assert index["index"]["structured"]["lines"] == 1
