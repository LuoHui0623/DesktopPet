from pathlib import Path

import pytest

from infra.files import io


def test_write_creates_parents_and_reads_back(tmp_path: Path):
    target = tmp_path / "a" / "b" / "draft.md"
    io.write_text(target, "你好\n世界")
    assert io.read_text(target) == "你好\n世界"


def test_read_lines_is_one_based_and_inclusive(tmp_path: Path):
    target = tmp_path / "draft.md"
    io.write_text(target, "一\n二\n三\n四")
    assert io.read_lines(target) == ["一", "二", "三", "四"]
    assert io.read_lines(target, 2, 3) == ["二", "三"]
    assert io.read_lines(target, 4) == ["四"]


def test_read_lines_rejects_invalid_range(tmp_path: Path):
    target = tmp_path / "draft.md"
    io.write_text(target, "一\n二")
    with pytest.raises(ValueError):
        io.read_lines(target, 0, 1)
    with pytest.raises(ValueError):
        io.read_lines(target, 2, 1)


def test_replace_paragraph_changes_only_that_paragraph(tmp_path: Path):
    target = tmp_path / "draft.md"
    io.write_text(target, "第一段\n仍是第一段\n\n第二段\n\n第三段")
    io.replace_paragraph(target, 2, "新的第二段\n多行")
    assert io.read_text(target) == "第一段\n仍是第一段\n\n新的第二段\n多行\n\n第三段"


def test_replace_paragraph_keeps_trailing_newline(tmp_path: Path):
    target = tmp_path / "draft.md"
    io.write_text(target, "甲\n\n乙\n")
    io.replace_paragraph(target, 1, "丙")
    assert io.read_text(target) == "丙\n\n乙\n"


def test_replace_paragraph_rejects_missing_paragraph(tmp_path: Path):
    target = tmp_path / "draft.md"
    io.write_text(target, "甲")
    with pytest.raises(ValueError, match="共 1 段"):
        io.replace_paragraph(target, 2, "乙")


def test_clear_empties_existing_file(tmp_path: Path):
    target = tmp_path / "draft.md"
    io.write_text(target, "内容")
    io.clear(target)
    assert io.read_text(target) == ""


def test_clear_missing_file_is_an_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        io.clear(tmp_path / "missing.md")


def test_io_does_not_judge_permissions(tmp_path: Path):
    outside = tmp_path / "outside.md"
    io.write_text(outside, "无需权限检查")
    assert io.read_text(outside) == "无需权限检查"
