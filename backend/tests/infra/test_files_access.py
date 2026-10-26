import os
import subprocess
from pathlib import Path

import pytest

from constants.path import RESOURCE_DIR
from infra.files.access import AccessDenied, check_read, check_write


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "resources"
    directory.mkdir()
    return directory


def test_default_root_is_resource_dir():
    assert check_read("草稿本.md").is_relative_to(RESOURCE_DIR.resolve())
    assert check_write("草稿本.md").is_relative_to(RESOURCE_DIR.resolve())


def test_relative_path_inside_root_is_allowed_for_read_and_write(root: Path):
    assert check_read("draft.md", roots=[root]) == (root / "draft.md").resolve()
    assert check_write("sub/draft.md", roots=[root]) == (root / "sub" / "draft.md").resolve()


def test_absolute_path_inside_root_is_allowed(root: Path):
    inside = root / "draft.md"
    assert check_write(inside, roots=[root]) == inside.resolve()


def test_absolute_path_outside_root_is_denied_with_reason(root: Path, tmp_path: Path):
    outside = tmp_path / "secret.md"
    with pytest.raises(AccessDenied) as exc:
        check_read(outside, roots=[root])
    assert "读取被拒绝" in exc.value.reason
    assert "不在允许目录内" in exc.value.reason


def test_parent_traversal_is_denied_with_reason(root: Path):
    with pytest.raises(AccessDenied) as exc:
        check_write("../secret.md", roots=[root])
    assert "写入被拒绝" in exc.value.reason
    assert ".." in exc.value.reason


def test_symlink_pointing_outside_is_denied(root: Path, tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("外部内容", encoding="utf-8")
    link = root / "link.md"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")
    with pytest.raises(AccessDenied) as exc:
        check_read(link, roots=[root])
    assert "符号链接" in exc.value.reason


def test_directory_symlink_escape_is_denied_for_write(root: Path, tmp_path: Path):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    link_dir = root / "linked"
    try:
        os.symlink(outside_dir, link_dir, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")
    with pytest.raises(AccessDenied):
        check_write(link_dir / "new.md", roots=[root])
    assert not (outside_dir / "new.md").exists()


def test_junction_escape_is_denied_for_write(root: Path, tmp_path: Path):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    link_dir = root / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_dir), str(outside_dir)],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("当前环境无法创建目录联接")
    with pytest.raises(AccessDenied) as exc:
        check_write(link_dir / "new.md", roots=[root])
    assert "符号链接" in exc.value.reason
    assert not (outside_dir / "new.md").exists()


def test_symlink_inside_root_is_allowed(root: Path):
    target = root / "real.md"
    target.write_text("内部", encoding="utf-8")
    link = root / "alias.md"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")
    assert check_read(link, roots=[root]) == target.resolve()


def test_read_and_write_are_checked_separately(root: Path, tmp_path: Path):
    readable = tmp_path / "readable"
    readable.mkdir()
    target = readable / "doc.md"
    target.write_text("只读", encoding="utf-8")
    assert check_read(target, roots=[readable]) == target.resolve()
    with pytest.raises(AccessDenied):
        check_write(target, roots=[root])
