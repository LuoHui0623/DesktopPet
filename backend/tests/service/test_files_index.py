"""T8：Index 分层、两视图与序列化。"""

import json

import pytest

from constants.files import DIR_ACTIONS, FILE_ACTIONS
from module.files.resolver import FileError

OLD_KEYS = {"kind", "resources", "skills", "actions", "parameters", "limits", "next"}


def dump(raw: str) -> dict:
    return json.loads(raw)


def prepare(env):
    (env.world / "笔记.md").write_text("a\n", encoding="utf-8")
    (env.world / "子目录").mkdir()
    (env.world / "子目录" / "内部.md").write_text("b\n", encoding="utf-8")


def test_domain_view_is_case_insensitive_and_has_only_new_keys(env):
    prepare(env)
    upper = dump(env.service.index("FILES"))
    lower = dump(env.service.index("files"))

    assert upper == lower
    assert set(upper) == {"support"}
    assert OLD_KEYS.isdisjoint(upper)
    support = upper["support"]
    assert support["dir_resources"] == ["world"]
    assert {item["action"] for item in support["dir_actions"]} == set(DIR_ACTIONS)
    assert {item["action"] for item in support["file_actions"]} == set(FILE_ACTIONS)
    for item in support["dir_actions"] + support["file_actions"]:
        assert set(item) == {"action", "args", "desc"}


def test_directory_view_types_paths_and_status(env):
    prepare(env)
    view = dump(env.service.index("world"))

    assert set(view) == {"resource_id", "entries"}
    assert view["resource_id"] == "world"
    assert OLD_KEYS.isdisjoint(view)
    entries = view["entries"]
    dirs = [item for item in entries if item["type"] == "dir"]
    files = [item for item in entries if item["type"] == "file"]
    assert dirs and files
    assert dirs[0]["path"].endswith(("\\", "/"))
    assert all(not item["path"].endswith(("\\", "/")) for item in files)
    assert all("editing_status" not in item for item in dirs)
    status = next(item for item in files if item["name"] == "笔记.md")["editing_status"]
    assert status["state"] == "free"
    assert status["notice"]
    assert all(set(item["action_permissions"]) <= set(DIR_ACTIONS) for item in dirs)
    assert all(set(item["action_permissions"]) <= set(FILE_ACTIONS) for item in files)


def test_subdirectory_can_be_indexed_by_relative_path(env):
    prepare(env)
    view = dump(env.service.index("子目录"))
    assert view["resource_id"] == "world"
    assert [item["name"] for item in view["entries"]] == ["内部.md"]


def test_unregistered_and_non_directory_errors_differ(env):
    with pytest.raises(FileError, match="不是可索引目录"):
        env.service.index("draft")
    with pytest.raises(FileError, match="未注册的目录"):
        env.service.index("并不存在")


def test_entries_are_returned_in_full_without_truncation_fields(env):
    prepare(env)
    for index in range(40):
        (env.world / f"file-{index:02d}.md").write_text("x\n", encoding="utf-8")
    view = dump(env.service.index("world"))
    assert len([item for item in view["entries"] if item["type"] == "file"]) == 42
    assert "truncated" not in view
    assert "next_offset" not in view


def test_editing_status_tracks_occupancy_in_directory_view(env):
    target = env.world / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "A", path="笔记.md")

    from conftest import make_task

    from module.agent.context import task_scope

    other = env.bind(make_task("main-2"))
    with task_scope(other):
        view = dump(env.service.index("world"))
    status = next(item for item in view["entries"] if item["name"] == "笔记.md")["editing_status"]
    assert status["state"] == "occupied"
    assert status["editing_by"] == {"task_id": "main-1"}
