"""T10：条目归一、合并、撤回与提交基底。"""

import pytest

from constants.files import (
    LINES_APPEND,
    LINES_DELETE,
    LINES_INSERT,
    LINES_REPLACE,
)
from module.files.ranges import ChangeQueue, apply_actions
from module.files.resolver import FileError


def test_every_write_action_normalizes_to_four_lines_ops(env):
    target = env.world / "笔记.md"
    target.write_text("a\nfoo\nb\nfoo\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.append("world", "尾", path="笔记.md")
    env.service.insert("world", 1, "头", path="笔记.md")
    env.service.replace_regex("world", [[1, 4]], "foo", "bar", path="笔记.md")
    env.service.create_file("world", "新建.md", "初始")
    env.service.delete_file("world", path="笔记.md")

    entries = env.session().queue.entries()
    assert {entry.op for entry in entries} == {
        LINES_APPEND,
        LINES_INSERT,
        LINES_DELETE,
        LINES_REPLACE,
    }
    assert sum(1 for entry in entries if entry.source_action == "replace_regex") == 2
    created = next(entry for entry in entries if entry.source_action == "create_file")
    assert created.op == LINES_APPEND and created.creates_file
    removed = next(entry for entry in entries if entry.source_action == "delete_file")
    assert removed.op == LINES_DELETE and removed.removes_file


def test_entries_in_one_hunk_do_not_depend_on_enqueue_order():
    def run(reverse: bool) -> tuple[int, list[str]]:
        queue = ChangeQueue()
        proposals = [
            dict(op=LINES_REPLACE, range_lines=(1, 2), content=("A",)),
            dict(op=LINES_REPLACE, range_lines=(3, 4), content=("C",)),
        ]
        for item in reversed(proposals) if reverse else proposals:
            queue.propose(
                resource="world",
                path="a.md",
                abs_path="/a.md",
                source_action="replace_lines",
                **item,
            )
        actions = queue.actions_for("/a.md", states=frozenset({"proposed"}))
        return len(actions), apply_actions(["a", "b", "c"], actions)

    # 中间只隔一行，属于同一 hunk，操作合并为一条 action，结果与入队顺序无关。
    assert run(False) == run(True) == (1, ["A", "b", "C"])


def test_hunk_gap_merges_operations_and_seven_lines_splits():
    def actions(second_start: int) -> int:
        queue = ChangeQueue()
        for span, text in (((1, 2), "A"), ((second_start, second_start + 1), "B")):
            entry = queue.propose(
                resource="world",
                path="a.md",
                abs_path="/a.md",
                op=LINES_REPLACE,
                source_action="replace_lines",
                range_lines=span,
                content=(text,),
            )
            queue.approve([entry.entry_id])
        return len(queue.actions_for("/a.md"))

    assert actions(8) == 1  # 两段之间 6 行未改动，同一 hunk，合并操作
    assert actions(9) == 2  # 7 行未改动即断开，各自成 action


def test_overlap_keeps_later_entry_and_marks_it_covered():
    queue = ChangeQueue()
    first = queue.propose(
        resource="world",
        path="a.md",
        abs_path="/a.md",
        op=LINES_REPLACE,
        source_action="replace_lines",
        range_lines=(1, 3),
        content=("X",),
    )
    second = queue.propose(
        resource="world",
        path="a.md",
        abs_path="/a.md",
        op=LINES_REPLACE,
        source_action="replace_lines",
        range_lines=(2, 3),
        content=("Y",),
    )
    queue.approve([first.entry_id, second.entry_id])
    actions = queue.actions_for("/a.md")
    assert len(actions) == 1
    assert actions[0].range_lines == (1, 3)
    assert apply_actions(["a", "b", "c"], actions) == ["X", "Y", "c"]
    assert queue.covered_ids("/a.md") == (first.entry_id,)


def test_withdraw_only_changes_its_own_range():
    queue = ChangeQueue()
    keep = queue.propose(
        resource="world",
        path="a.md",
        abs_path="/a.md",
        op=LINES_REPLACE,
        source_action="replace_lines",
        range_lines=(1, 2),
        content=("A",),
    )
    drop = queue.propose(
        resource="world",
        path="a.md",
        abs_path="/a.md",
        op=LINES_REPLACE,
        source_action="replace_lines",
        range_lines=(3, 4),
        content=("C",),
    )
    queue.approve([keep.entry_id, drop.entry_id])
    queue.withdraw([drop.entry_id])

    actions = queue.actions_for("/a.md")
    assert len(actions) == 1
    assert apply_actions(["a", "b", "c"], actions) == ["A", "b", "c"]
    assert not queue.covered_ids("/a.md")


def test_append_keeps_trailing_newline_without_blank_line(env):
    target = env.world / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.append("world", "c", path="笔记.md")
    entry = env.session().queue.entries_for(str(target.resolve()))[-1]
    env.session().queue.approve([entry.entry_id])

    env.review.settle("main-1")

    assert target.read_text(encoding="utf-8") == "a\nb\nc\n"


def test_disk_is_untouched_before_commit(env):
    target = env.world / "笔记.md"
    target.write_text("a\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "A", path="笔记.md")
    env.service.append("world", "b", path="笔记.md")
    assert target.read_text(encoding="utf-8") == "a\n"

    env.session().queue.approve([entry.entry_id for entry in env.session().queue.entries()])
    assert target.read_text(encoding="utf-8") == "a\n"
    env.review.settle("main-1")
    assert target.read_text(encoding="utf-8") == "A\nb\n"


def test_disjoint_entry_survives_external_change_and_is_offset(env):
    target = env.world / "笔记.md"
    target.write_text("a\nb\nc\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[3, 3]], "C", path="笔记.md")
    session = env.session()
    abs_path = str(target.resolve())
    kept = session.queue.entries_for(abs_path)[0]

    target.write_text("NEW\na\nb\nc\n", encoding="utf-8")
    env.service.read("world", "笔记.md")

    refreshed = session.queue.entry(kept.entry_id)
    assert refreshed is not None and refreshed.state == "proposed"
    assert refreshed.range_lines == (4, 5)


def test_create_file_conflicts_when_target_appears_before_commit(env):
    target = env.world / "新建.md"
    env.service.create_file("world", "新建.md", "内容")
    entries = env.session().queue.entries()
    assert entries[0].creates_file
    assert not target.exists()

    target.write_text("外部先建\n", encoding="utf-8")
    with pytest.raises(FileError, match="已存在"):
        env.service.create_file("world", "新建.md", "重复")

    env.session().queue.approve([entries[0].entry_id])
    outcome = env.review.settle("main-1")
    assert outcome.results[0].status == "conflict"
    assert target.read_text(encoding="utf-8") == "外部先建\n"
