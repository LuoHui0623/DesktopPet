"""T11：按行 diff、hunk 分段、标注与 review 两层视图。"""

from module.files.ranges import ChangeQueue
from module.files.review import (
    DECORATION_INSERT,
    KIND_ADD,
    KIND_REMOVE,
    FileState,
    build_review,
)
from module.files.text import text_state

PATH = "/a.md"


def file_state(text: str):
    return FileState("world", "a.md", PATH, text_state(text)[0], True)


def propose(queue: ChangeQueue, span, content, *, action="replace_lines"):
    return queue.propose(
        resource="world",
        path="a.md",
        abs_path=PATH,
        op="replace_lines",
        source_action=action,
        range_lines=span,
        content=content,
    )


def lines_of(change):
    return [line for hunk in change.hunks for line in hunk.lines]


def test_hunk_merges_its_operations_into_one_action():
    queue = ChangeQueue()
    first = propose(queue, (1, 2), ("A",))
    second = propose(queue, (3, 4), ("C",))
    queue.approve([first.entry_id, second.entry_id])

    view = build_review([file_state("a\nb\nc\n")], queue, include=frozenset({"approved"}))
    change = view.files[0]
    assert len(change.hunks) == 1
    actions = queue.actions_for(PATH)
    assert len(actions) == 1  # 同一 hunk 内的操作合并，审批一起进行
    assert set(actions[0].entry_ids) == {first.entry_id, second.entry_id}
    added = [line for line in lines_of(change) if line.kind == KIND_ADD]
    assert [line.text for line in added] == ["A", "C"]
    traced = {annotation.source_action for line in added for annotation in line.annotations}
    assert traced == {"replace_lines"}
    action_ids = {annotation.action for line in added for annotation in line.annotations}
    assert action_ids == {0}


def test_merge_gap_threshold_is_six_lines():
    def counts(second_start: int) -> tuple[int, int]:
        base = "\n".join(f"l{index}" for index in range(1, 22)) + "\n"
        queue = ChangeQueue()
        first = propose(queue, (1, 2), ("A",))
        second = propose(queue, (second_start, second_start + 1), ("B",))
        queue.approve([first.entry_id, second.entry_id])
        view = build_review([file_state(base)], queue, include=frozenset({"approved"}))
        return len(view.files[0].hunks), len(queue.actions_for(PATH))

    assert counts(8) == (1, 1)  # 改动行之间 6 行未改动：同一 hunk，操作合并
    assert counts(9) == (2, 2)  # 7 行未改动即断开：两个 hunk，两条 action


def test_input_decoration_is_not_counted_as_an_extra_line():
    queue = ChangeQueue()
    entry = queue.propose(
        resource="world",
        path="a.md",
        abs_path=PATH,
        op="insert",
        source_action="insert",
        range_lines=(2, 2),
        content=("X", "Y"),
    )
    queue.approve([entry.entry_id])

    change = build_review([file_state("a\nb\n")], queue, include=frozenset({"approved"})).files[0]
    rows = lines_of(change)
    decorated = [row for row in rows if row.decoration == DECORATION_INSERT]
    assert len(decorated) == 1
    assert decorated[0].kind == KIND_ADD
    assert change.added == 2
    assert change.removed == 0
    assert len([row for row in rows if row.kind == KIND_ADD]) == 2


def test_partial_approval_only_shows_and_commits_approved(env):
    target = env.world / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "A", path="笔记.md")
    env.service.replace_lines("world", [[2, 2]], "B", path="笔记.md")
    session = env.session()
    entries = session.queue.entries_for(str(target.resolve()))
    session.queue.approve([entries[0].entry_id])
    session.queue.reject([entries[1].entry_id])

    approved = env.review.review("main-1")
    assert approved.files[0].added == 1
    assert approved.files[0].removed == 1
    texts = [line.text for line in lines_of(approved.files[0]) if line.kind == KIND_ADD]
    assert texts == ["A"]

    pending = env.review.pending("main-1")
    pending_texts = [
        line.text for line in lines_of(pending.files[0]) if line.kind in (KIND_ADD, KIND_REMOVE)
    ]
    assert "B" not in pending_texts
    assert session.queue.actions_for(str(target.resolve()))[0].entry_ids == (entries[0].entry_id,)


def test_withdraw_removes_entry_from_both_views(env):
    target = env.world / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "A", path="笔记.md")
    entry = env.session().queue.entries_for(str(target.resolve()))[0]
    env.session().queue.approve([entry.entry_id])
    assert env.review.review("main-1").files

    env.review.withdraw("main-1", [entry.entry_id])

    assert not env.review.review("main-1").files
    assert not env.review.pending("main-1").files


def test_directory_entries_only_show_chosen_states(env):
    env.service.create_dir("world", "甲")
    env.service.create_dir("world", "乙")
    session = env.session()
    first = session.queue.structure_entries()[0]
    session.queue.approve([first.entry_id])

    approved = env.review.review("main-1")
    assert [change.path for change in approved.dirs] == [first.path]
    assert approved.dirs[0].mark == "A"

    pending = env.review.pending("main-1")
    assert len(pending.dirs) == 2


def test_rejected_entry_is_neither_shown_nor_committed(env):
    target = env.world / "笔记.md"
    target.write_text("a\n", encoding="utf-8")
    env.service.read("world", "笔记.md")
    env.service.replace_lines("world", [[1, 1]], "A", path="笔记.md")
    entry = env.session().queue.entries_for(str(target.resolve()))[0]
    env.session().queue.reject([entry.entry_id])

    assert not env.review.review("main-1").files
    assert not env.review.pending("main-1").files
    outcome = env.review.settle("main-1")
    assert not outcome.results
    assert target.read_text(encoding="utf-8") == "a\n"
