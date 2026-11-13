"""T7：动作集拆分与目录动作。"""

import pytest
from conftest import make_permissions, make_task

from constants.files import (
    ACTION_APPEND,
    ACTION_CREATE_DIR,
    ACTION_CREATE_FILE,
    ACTION_DELETE_DIR,
    ACTION_DELETE_FILE,
    ACTION_DELETE_LINES,
    ACTION_INSERT,
    ACTION_ORDER,
    ACTION_REPLACE_LINES,
    ACTION_REPLACE_REGEX,
    DIR_ACTIONS,
    FILE_ACTIONS,
    WORLD,
)
from module.agent.context import task_scope
from module.files.resolver import FileDenied, FileError
from module.files.schemas import ACTION_DOCS


def test_actions_are_split_by_receptor_type():
    assert ACTION_DELETE_FILE in DIR_ACTIONS
    assert ACTION_CREATE_FILE in DIR_ACTIONS
    assert ACTION_DELETE_LINES in FILE_ACTIONS
    assert ACTION_DELETE_FILE not in FILE_ACTIONS
    assert "edit" not in ACTION_ORDER
    assert set(DIR_ACTIONS) | set(FILE_ACTIONS) == set(ACTION_ORDER)


def test_append_doc_and_delete_dir_doc_state_their_limits():
    first = ACTION_DOCS[ACTION_APPEND].split("\n", 1)[0]
    assert "末尾" in first and "不新建文件" in first
    assert "非空" in ACTION_DOCS[ACTION_DELETE_DIR]
    assert "不递归" in ACTION_DOCS[ACTION_DELETE_DIR]


def test_append_on_missing_file_asks_for_create_file(env):
    env.service.read("world", "缺省.md")
    with pytest.raises(FileError, match="create_file"):
        env.service.append("world", "内容", path="缺省.md")


def test_delete_file_needs_its_own_permission(env):
    (env.world / "笔记.md").write_text("a\nb\n", encoding="utf-8")
    permissions = make_permissions(without=("delete_file",))
    restricted = env.bind(make_task("main-limited", permissions=permissions))
    with task_scope(restricted):
        env.service.read("world", "笔记.md")
        env.service.delete_lines("world", [[1, 2]], path="笔记.md")
        with pytest.raises(FileDenied, match="delete_file"):
            env.service.delete_file("world", path="笔记.md")


def test_delete_lines_empties_file_and_keeps_it(env):
    target = env.world / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.delete_lines("world", [[1, 2]], path="笔记.md")
    session = env.session()
    entry = session.queue.entries_for(str(target.resolve()))[-1]
    session.queue.approve([entry.entry_id])

    env.review.settle("main-1")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == ""


def test_delete_file_removes_file_body(env):
    target = env.world / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.delete_file("world", path="笔记.md")
    session = env.session()
    entry = session.queue.entries_for(str(target.resolve()))[-1]
    session.queue.approve([entry.entry_id])

    env.review.settle("main-1")

    assert not target.exists()


def test_create_and_delete_dir_only_through_queue(env):
    env.service.create_dir("world", "新目录")
    created = env.world / "新目录"
    assert not created.exists()
    session = env.session()
    entry = session.queue.structure_entries()[0]
    session.queue.approve([entry.entry_id])

    env.review.settle("main-1")

    assert created.is_dir()


def test_delete_dir_refuses_non_empty_directory(env):
    target = env.world / "有货"
    target.mkdir()
    (target / "a.md").write_text("a\n", encoding="utf-8")
    (target / "b.md").write_text("b\n", encoding="utf-8")

    with pytest.raises(FileError, match="2"):
        env.service.delete_dir("world", "有货")
    assert target.exists()


def test_delete_dir_applies_to_empty_directory(env):
    target = env.world / "空目录"
    target.mkdir()
    env.service.delete_dir("world", "空目录")
    session = env.session()
    entry = session.queue.structure_entries()[0]
    session.queue.approve([entry.entry_id])

    env.review.settle("main-1")

    assert not target.exists()


def test_each_action_checks_its_own_permission(env):
    permissions = make_permissions(without=("append",))
    restricted = env.bind(make_task("main-write-denied", permissions=permissions))
    (env.world / "笔记.md").write_text("a\n", encoding="utf-8")
    with task_scope(restricted):
        env.service.read("world", "笔记.md")
        with pytest.raises(FileDenied, match="append"):
            env.service.append("world", "b", path="笔记.md")
        # 同一任务仍有 delete_lines 权限时不受影响。
        env.service.delete_lines("world", [[1, 1]], path="笔记.md")


def test_world_supports_all_write_actions_but_draft_does_not_delete_file():
    from constants.files import RESOURCES

    assert ACTION_APPEND in RESOURCES[WORLD].edit_supports
    assert ACTION_INSERT in RESOURCES[WORLD].edit_supports
    assert ACTION_REPLACE_LINES in RESOURCES[WORLD].edit_supports
    assert ACTION_REPLACE_REGEX in RESOURCES[WORLD].edit_supports
    assert ACTION_DELETE_LINES in RESOURCES[WORLD].edit_supports
    assert ACTION_CREATE_DIR in RESOURCES[WORLD].edit_supports
    assert ACTION_DELETE_DIR in RESOURCES[WORLD].edit_supports
    assert not RESOURCES["draft"].can_delete_file
