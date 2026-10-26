from pathlib import Path

import pytest

from service.agent.tools.file import FILE_TOOLS, make_file_tools


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "resources"
    directory.mkdir()
    return directory


@pytest.fixture
def tools(root: Path):
    by_name = {tool.name: tool for tool in make_file_tools(root)}
    return by_name


def test_exposes_four_tools():
    assert {tool.name for tool in FILE_TOOLS} == {
        "file_read",
        "file_write",
        "file_edit",
        "file_clear",
    }


def test_write_then_read_returns_structured_results(tools, root: Path):
    written = tools["file_write"].invoke({"path": "draft.md", "content": "第一段\n\n第二段"})
    assert written == {
        "ok": True,
        "action": "write",
        "path": "draft.md",
        "content": None,
        "error": None,
    }
    assert (root / "draft.md").read_text(encoding="utf-8") == "第一段\n\n第二段"

    read = tools["file_read"].invoke({"path": "draft.md"})
    assert read["ok"] is True
    assert read["action"] == "read"
    assert read["content"] == "第一段\n\n第二段"


def test_read_with_line_range(tools):
    tools["file_write"].invoke({"path": "draft.md", "content": "一\n二\n三"})
    result = tools["file_read"].invoke({"path": "draft.md", "start": 2, "end": 3})
    assert result["ok"] is True
    assert result["content"] == "二\n三"


def test_edit_replaces_one_paragraph(tools):
    tools["file_write"].invoke({"path": "draft.md", "content": "甲\n\n乙"})
    result = tools["file_edit"].invoke({"path": "draft.md", "index": 2, "content": "丙"})
    assert result["ok"] is True
    assert tools["file_read"].invoke({"path": "draft.md"})["content"] == "甲\n\n丙"


def test_clear_empties_file_and_keeps_it(tools, root: Path):
    tools["file_write"].invoke({"path": "draft.md", "content": "内容"})
    result = tools["file_clear"].invoke({"path": "draft.md"})
    assert result["ok"] is True
    assert (root / "draft.md").exists()
    assert (root / "draft.md").read_text(encoding="utf-8") == ""


def test_missing_file_returns_readable_reason(tools):
    result = tools["file_read"].invoke({"path": "missing.md"})
    assert result["ok"] is False
    assert result["error"] == "文件不存在：missing.md"


def test_out_of_range_edit_returns_reason(tools):
    tools["file_write"].invoke({"path": "draft.md", "content": "甲"})
    result = tools["file_edit"].invoke({"path": "draft.md", "index": 5, "content": "乙"})
    assert result["ok"] is False
    assert "共 1 段" in result["error"]


def test_path_traversal_is_denied_and_does_not_write(tools, root: Path):
    result = tools["file_write"].invoke({"path": "../escaped.md", "content": "越界"})
    assert result["ok"] is False
    assert ".." in result["error"]
    assert not (root.parent / "escaped.md").exists()


def test_absolute_path_outside_root_is_denied_and_does_not_write(tools, root: Path, tmp_path: Path):
    outside = tmp_path / "outside.md"
    result = tools["file_write"].invoke({"path": str(outside), "content": "越界"})
    assert result["ok"] is False
    assert "不在允许目录内" in result["error"]
    assert not outside.exists()


def test_denied_edit_and_clear_do_not_touch_existing_file(tools, root: Path, tmp_path: Path):
    outside = tmp_path / "keep.md"
    outside.write_text("保留", encoding="utf-8")
    assert (
        tools["file_edit"].invoke({"path": str(outside), "index": 1, "content": "x"})["ok"] is False
    )
    assert tools["file_clear"].invoke({"path": str(outside)})["ok"] is False
    assert outside.read_text(encoding="utf-8") == "保留"


def test_tools_expose_argument_schemas(tools):
    schema = tools["file_edit"].args
    assert {"path", "index", "content"} <= set(schema)
    assert tools["file_read"].description.startswith("读取")
