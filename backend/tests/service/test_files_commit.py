"""提交器：从 `H0` 投影已批准 action，外部变化一律拒绝、不做三方合并。"""

from pathlib import Path

from constants.files import LINES_DELETE, LINES_REPLACE
from module.files.commit import commit_file
from module.files.session import TaskSession


def stage(
    target: Path,
    *,
    base: str,
    content: tuple[str, ...] = ("B",),
    span: tuple[int, int] = (2, 3),
    op: str = LINES_REPLACE,
    exists: bool = True,
    creates: bool = False,
    removes: bool = False,
):
    """建立一个已批准的 action，返回提交器需要的 tracked 与 actions。"""
    session = TaskSession()
    tracked = session.track(
        resource="world",
        path=target.name,
        abs_path=str(target.resolve()),
        text=base,
        exists=exists,
        creates_file=creates,
        removes_file=removes,
    )
    entry = session.queue.propose(
        resource="world",
        path=tracked.path,
        abs_path=tracked.abs_path,
        op=op,
        source_action="delete_file" if removes else "replace_lines",
        range_lines=span,
        content=content,
        creates_file=creates,
        removes_file=removes,
    )
    session.queue.approve([entry.entry_id])
    return tracked, session.queue.actions_for(tracked.abs_path)


def test_commit_writes_when_disk_matches_base(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    tracked, actions = stage(target, base="a\nb\n")

    result = commit_file(tracked, actions)

    assert result.ok is True
    assert result.written is True
    assert target.read_text(encoding="utf-8") == "a\nB\n"


def test_commit_rejects_external_change_without_merging(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("A\nb\nc\n", encoding="utf-8")
    tracked, actions = stage(target, base="a\nb\nc\n")

    result = commit_file(tracked, actions)

    assert result.ok is False
    assert result.written is False
    assert result.error == "检测到哈希不一致"
    assert result.conflicts
    assert target.read_text(encoding="utf-8") == "A\nb\nc\n"


def test_commit_keeps_trailing_newline_state(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\nb", encoding="utf-8")
    tracked, actions = stage(target, base="a\nb")

    assert commit_file(tracked, actions).ok is True
    assert target.read_text(encoding="utf-8") == "a\nB"


def test_commit_does_not_rewrite_unchanged_text(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    tracked, actions = stage(target, base="a\nb\n", content=("b",))

    result = commit_file(tracked, actions)

    assert result.ok is True
    assert result.written is False


def test_delete_file_removes_the_body(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")
    tracked, actions = stage(
        target,
        base="a\nb\n",
        span=(1, 3),
        op=LINES_DELETE,
        content=(),
        removes=True,
    )

    result = commit_file(tracked, actions)

    assert result.ok is True
    assert not target.exists()


def test_delete_file_rejects_when_disk_changed(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\n外部改过\n", encoding="utf-8")
    tracked, actions = stage(
        target,
        base="a\nb\n",
        span=(1, 3),
        op=LINES_DELETE,
        content=(),
        removes=True,
    )

    result = commit_file(tracked, actions)

    assert result.ok is False
    assert target.read_text(encoding="utf-8") == "a\n外部改过\n"


def test_create_file_conflicts_when_target_appears_externally(tmp_path: Path):
    target = tmp_path / "新文件.md"
    target.write_text("别人先建了\n", encoding="utf-8")
    tracked, actions = stage(
        target,
        base="",
        span=(1, 1),
        content=("草稿内容",),
        exists=False,
        creates=True,
    )

    result = commit_file(tracked, actions)

    assert result.ok is False
    assert result.written is False
    assert target.read_text(encoding="utf-8") == "别人先建了\n"


def test_create_file_writes_when_target_stays_absent(tmp_path: Path):
    target = tmp_path / "新文件.md"
    tracked, actions = stage(
        target,
        base="",
        span=(1, 1),
        content=("草稿内容",),
        exists=False,
        creates=True,
    )

    result = commit_file(tracked, actions)

    assert result.ok is True
    assert result.written is True
    assert target.read_text(encoding="utf-8") == "草稿内容\n"


def test_commit_reports_missing_target_without_creating_it(tmp_path: Path):
    target = tmp_path / "笔记.md"
    tracked, actions = stage(target, base="a\n")

    result = commit_file(tracked, actions)

    assert result.ok is False
    assert "已不存在" in (result.error or "")
    assert not target.exists()


def test_empty_actions_are_a_noop(tmp_path: Path):
    target = tmp_path / "笔记.md"
    tracked, _ = stage(target, base="a\n")

    result = commit_file(tracked, [])

    assert result.ok is True
    assert result.written is False
    assert not target.exists()
