import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.store.app_settings import DbAppSettings
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate
from service.memory.daily_log_service import DailyLogService
from service.memory.index import DESCRIPTIONS, MemoryIndexService, build_index
from service.memory.memory_service import StructuredMemoryDoc

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def stack(tmp_path: Path, db_file: Path):
    index_file = tmp_path / "memory" / "index.json"
    structured = StructuredMemoryDoc(tmp_path / "memory" / "structured.md")
    daily = DailyLogService(DbDailyLog(db_file), DbAppSettings(db_file), TZ)
    index = MemoryIndexService(structured.lines, daily.dates, index_file)
    structured.add_listener(index.rebuild)
    index.rebuild()
    return {
        "structured": structured,
        "daily": daily,
        "index": index,
        "index_file": index_file,
        "db_file": db_file,
    }


def read_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_build_index_has_documented_shape():
    index = build_index([], [])
    assert set(index) == {"descriptions", "index"}
    assert index["descriptions"] == DESCRIPTIONS
    assert index["index"] == {
        "structured": {"name": "structured", "lines": 0, "headings": []},
        "daily": {},
    }


def test_daily_memory_is_grouped_by_month_with_sorted_unique_dates():
    index = build_index([], ["2026-10-25", "2026-10-18", "2026-10-25", "2026-09-30"])
    assert index["index"]["daily"] == {
        "2026-09": ["2026-09-30"],
        "2026-10": ["2026-10-18", "2026-10-25"],
    }


def test_structured_index_counts_lines_and_headings():
    lines = ["## 主人信息", "名字：小李", "", "## 喜好", "喜欢美式"]
    assert build_index(lines, [])["index"]["structured"] == {
        "name": "structured",
        "lines": 5,
        "headings": [
            {"line": 1, "text": "## 主人信息"},
            {"line": 4, "text": "## 喜好"},
        ],
    }


def test_index_file_is_written_on_startup(stack):
    index = read_index(stack["index_file"])
    assert index == build_index([], [])


def test_saving_structured_doc_refreshes_index(stack):
    stack["structured"].save("## 主人信息\n名字：小李\n")
    index = read_index(stack["index_file"])
    assert index["index"]["structured"]["lines"] == 2
    assert index["index"]["structured"]["headings"] == [{"line": 1, "text": "## 主人信息"}]


def test_append_refreshes_index(stack):
    stack["structured"].append("咖啡偏好")
    index = read_index(stack["index_file"])
    assert index["index"]["structured"]["lines"] == 1


def test_rebuild_is_skipped_when_nothing_changed(stack):
    before = stack["index_file"].stat().st_mtime_ns
    stack["index"].rebuild()
    assert stack["index_file"].stat().st_mtime_ns == before


def test_external_file_removal_is_reflected_on_rebuild(stack):
    stack["structured"].append("咖啡偏好")
    stack["structured"].path.unlink()
    stack["index"].rebuild()
    assert read_index(stack["index_file"])["index"]["structured"]["lines"] == 0


def test_daily_memory_dates_come_from_daily_log(stack):
    store = DbDailyLog(stack["db_file"])
    store.append("2026-10-25", "2026-10-25T09:00:00+08:00", [("master", "早")])
    store.append("2026-10-18", "2026-10-18T09:00:00+08:00", [("master", "你好")])
    stack["index"].rebuild()
    assert read_index(stack["index_file"])["index"]["daily"] == {
        "2026-10": ["2026-10-18", "2026-10-25"]
    }


def test_index_matches_storage_exactly(stack):
    stack["structured"].append("咖啡偏好")
    stack["index"].rebuild()
    index = read_index(stack["index_file"])
    assert index == build_index(stack["structured"].lines(), stack["daily"].dates())


def test_index_is_written_atomically(stack):
    stack["structured"].append("咖啡偏好")
    assert list(stack["index_file"].parent.glob("*.tmp")) == []


def test_rebuild_on_empty_storage_writes_empty_index(stack):
    stack["structured"].save("")
    stack["index"].rebuild()
    assert read_index(stack["index_file"]) == build_index([], [])
