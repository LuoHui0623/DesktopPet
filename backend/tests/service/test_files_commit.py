from pathlib import Path

from module.files.commit import commit_change
from module.files.staging import StagedChange, StagedSnapshot

KIND = "world"
KEY = "world:笔记.md"


def snapshot(
    target: Path,
    *,
    base: str,
    staged: str,
    created: bool = False,
    delete_file: bool = False,
) -> StagedSnapshot:
    return StagedSnapshot(
        key=KEY,
        change=StagedChange(
            resource=KIND,
            path="笔记.md",
            op="delete_file" if delete_file else "replace_lines",
            author="sub-1",
            base_text=base,
            staged_text=staged,
        ),
        authors=("sub-1",),
        writes=1,
        created=created,
        delete_file=delete_file,
        abs_path=target,
    )


def test_commit_writes_staged_when_disk_matches_base(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")

    result = commit_change(snapshot(target, base="a\nb\n", staged="a\nB\n"))

    assert result.ok is True
    assert result.written is True
    assert result.merged is False
    assert target.read_text(encoding="utf-8") == "a\nB\n"


def test_commit_merges_disjoint_external_change(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("A\nb\nc\n", encoding="utf-8")

    result = commit_change(snapshot(target, base="a\nb\nc\n", staged="a\nb\nC\n"))

    assert result.ok is True
    assert result.written is True
    assert result.merged is True
    assert result.conflicts == ()
    assert target.read_text(encoding="utf-8") == "A\nb\nC\n"


def test_commit_conflict_keeps_disk_and_reports_both_sides(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\n磁盘\nc\n", encoding="utf-8")

    result = commit_change(snapshot(target, base="a\nb\nc\n", staged="a\n草稿\nc\n"))

    assert result.ok is False
    assert result.written is False
    assert "冲突" in (result.error or "")
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert (conflict.base_start, conflict.base_end) == (2, 2)
    assert conflict.disk == ("磁盘",)
    assert conflict.staged == ("草稿",)
    assert target.read_text(encoding="utf-8") == "a\n磁盘\nc\n"


def test_commit_does_not_rewrite_when_disk_already_has_staged(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\nB\n", encoding="utf-8")

    result = commit_change(snapshot(target, base="a\nb\n", staged="a\nB\n"))

    assert result.ok is True
    assert result.written is False
    assert target.read_text(encoding="utf-8") == "a\nB\n"


def test_commit_created_file_conflicts_with_external_content(tmp_path: Path):
    target = tmp_path / "新文件.md"
    target.write_text("别人先建了\n", encoding="utf-8")

    result = commit_change(snapshot(target, base="", staged="草稿内容\n", created=True))

    assert result.ok is False
    assert result.written is False
    assert result.conflicts
    assert target.read_text(encoding="utf-8") == "别人先建了\n"


def test_commit_created_file_write_when_external_file_is_empty(tmp_path: Path):
    target = tmp_path / "新文件.md"
    target.write_text("", encoding="utf-8")

    result = commit_change(snapshot(target, base="", staged="草稿内容\n", created=True))

    assert result.ok is True
    assert result.written is True
    assert target.read_text(encoding="utf-8") == "草稿内容\n"


def test_commit_delete_conflicts_when_disk_changed(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\n外部改过\n", encoding="utf-8")

    result = commit_change(snapshot(target, base="a\nb\n", staged="", delete_file=True))

    assert result.ok is False
    assert result.written is False
    assert result.conflicts[0].disk == ("a", "外部改过", "")
    assert result.conflicts[0].staged == ()
    assert target.read_text(encoding="utf-8") == "a\n外部改过\n"


def test_commit_delete_removes_file_when_disk_matches_base(tmp_path: Path):
    target = tmp_path / "笔记.md"
    target.write_text("a\nb\n", encoding="utf-8")

    result = commit_change(snapshot(target, base="a\nb\n", staged="", delete_file=True))

    assert result.ok is True
    assert result.written is True
    assert not target.exists()


def test_commit_reports_missing_target_without_creating_it(tmp_path: Path):
    target = tmp_path / "笔记.md"

    result = commit_change(snapshot(target, base="a\n", staged="b\n"))

    assert result.ok is False
    assert result.written is False
    assert not target.exists()
