from pathlib import Path

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
