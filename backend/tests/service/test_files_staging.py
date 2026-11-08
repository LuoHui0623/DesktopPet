from pathlib import Path

import pytest

from module.files.resolver import FileError
from module.files.staging import (
    MAX_FILE_LINES,
    StagingStore,
    TaskStaging,
)


def establish(staging: TaskStaging, text: str = "一\n二\n") -> None:
    staging.establish(
        resource="world",
        key="world:笔记.md",
        display="笔记.md",
        abs_path=Path("/tmp/笔记.md"),
        text=text,
    )


def test_establish_keeps_base_and_staged_equal_before_write():
    staging = TaskStaging()
    establish(staging)
    snapshot = staging.get("world:笔记.md")
    assert snapshot is not None
    assert snapshot.change.base_text == "一\n二\n"
    assert snapshot.change.staged_text == "一\n二\n"
    assert snapshot.dirty is False


def test_record_updates_staged_text_author_and_writes():
    staging = TaskStaging()
    establish(staging)
    staging.record(key="world:笔记.md", op="append", author="sub-1", staged_text="一\n二\n三\n")
    snapshot = staging.get("world:笔记.md")
    assert snapshot is not None
    assert snapshot.change.staged_text == "一\n二\n三\n"
    assert snapshot.change.op == "append"
    assert snapshot.change.author == "sub-1"
    assert snapshot.authors == ("sub-1",)
    assert snapshot.writes == 1
    assert snapshot.dirty is True
    assert staging.active("sub-1") == "world:笔记.md"


def test_repeated_read_refreshes_base_until_first_write():
    staging = TaskStaging()
    establish(staging, "旧")
    establish(staging, "新")
    assert staging.get("world:笔记.md").change.base_text == "新"
    staging.record(key="world:笔记.md", op="append", author="sub-1", staged_text="新\n追加\n")
    establish(staging, "外部改动")
    assert staging.get("world:笔记.md").change.base_text == "新"
    assert staging.get("world:笔记.md").change.staged_text == "新\n追加\n"


def test_pending_only_contains_dirty_entries_sorted_by_display_path():
    staging = TaskStaging()
    establish(staging)
    staging.establish(
        resource="world",
        key="world:甲.md",
        display="甲.md",
        abs_path=Path("/tmp/甲.md"),
        text="a\n",
    )
    staging.record(key="world:甲.md", op="append", author="sub-1", staged_text="a\nb\n")
    assert [item.change.path for item in staging.pending()] == ["甲.md"]


def test_delete_file_is_a_dirty_staged_change():
    staging = TaskStaging()
    establish(staging)
    staging.record(
        key="world:笔记.md",
        op="delete_file",
        author="sub-1",
        staged_text="",
        delete_file=True,
    )
    snapshot = staging.get("world:笔记.md")
    assert snapshot is not None
    assert snapshot.delete_file is True
    assert snapshot.dirty is True
    assert snapshot.change.staged_text == ""


def test_record_rejects_oversized_files():
    staging = TaskStaging()
    establish(staging, "x")
    huge = "\n".join(str(index) for index in range(MAX_FILE_LINES + 1))
    with pytest.raises(FileError, match="行"):
        staging.record(key="world:笔记.md", op="append", author="sub-1", staged_text=huge)


def test_store_isolates_tasks_and_releases_them():
    store = StagingStore()
    store.for_task("a").establish(
        resource="world",
        key="world:笔记.md",
        display="笔记.md",
        abs_path=Path("/tmp/a.md"),
        text="a",
    )
    assert store.for_task("b").get("world:笔记.md") is None
    store.release("a")
    assert store.for_task("a").get("world:笔记.md") is None
