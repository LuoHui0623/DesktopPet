from pathlib import Path

from constants.path import ROOT_DIR
from module.agent.prompt import default_resources
from module.agent.workspace import DRAFT_TEMPLATE, prepare_workspace


def test_prepare_workspace_creates_dirs_and_draft(tmp_path: Path):
    resource_dir = tmp_path / "resources"
    draft = prepare_workspace(resource_dir)
    assert draft == resource_dir / "草稿本.md"
    assert draft.read_text(encoding="utf-8") == DRAFT_TEMPLATE
    assert (resource_dir / "表情包").is_dir()


def test_prepare_workspace_is_idempotent_and_keeps_notes(tmp_path: Path):
    resource_dir = tmp_path / "resources"
    draft = prepare_workspace(resource_dir)
    draft.write_text("# 我的便签\n\n- 记得问主人晚饭", encoding="utf-8")
    prepare_workspace(resource_dir)
    assert draft.read_text(encoding="utf-8") == "# 我的便签\n\n- 记得问主人晚饭"


def test_prepare_workspace_recreates_missing_draft(tmp_path: Path):
    resource_dir = tmp_path / "resources"
    draft = prepare_workspace(resource_dir)
    draft.unlink()
    prepare_workspace(resource_dir)
    assert draft.read_text(encoding="utf-8") == DRAFT_TEMPLATE


def test_resources_follow_the_given_resource_dir(tmp_path: Path):
    items = default_resources(tmp_path / "resources")
    assert [item.label for item in items] == ["草稿本", "表情包"]
    assert items[0].path == (tmp_path / "resources" / "草稿本.md").as_posix()
    assert items[1].path == (tmp_path / "resources" / "表情包").as_posix()


def test_resources_inside_the_repo_are_relative(tmp_path: Path):
    items = default_resources(ROOT_DIR / "backend" / "data" / "resources")
    assert items[0].path == "backend/data/resources/草稿本.md"
