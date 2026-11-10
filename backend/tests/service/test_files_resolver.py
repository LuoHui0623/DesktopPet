import os
import subprocess
from pathlib import Path

import pytest

from constants.files import (
    ACTION_APPEND,
    ACTION_DELETE,
    ACTION_READ,
    ACTION_REPLACE_LINES,
    DRAFT,
    EDIT_ACTIONS,
    PERMANENT,
    READ_ACTIONS,
    RESOURCES,
    STRUCTURED,
    WORLD,
)
from module.files.resolver import ResourceResolver


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "resources").mkdir(parents=True)
    (data / "resources" / "草稿本.md").write_text("# 草稿本\n", encoding="utf-8")
    (data / "resources" / "笔记.md").write_text("便签\n", encoding="utf-8")
    return data


@pytest.fixture
def resolver(data_dir: Path) -> ResourceResolver:
    return ResourceResolver(data_dir)


def test_registry_declares_ids_paths_and_permissions():
    assert set(RESOURCES) == {DRAFT, STRUCTURED, PERMANENT, WORLD}
    assert RESOURCES[DRAFT].path == "resources/草稿本.md"
    assert RESOURCES[STRUCTURED].path is None
    assert RESOURCES[STRUCTURED].actions == frozenset(READ_ACTIONS)
    assert RESOURCES[STRUCTURED].edit_supports == ()
    assert RESOURCES[PERMANENT].path is None
    assert RESOURCES[PERMANENT].actions == frozenset(READ_ACTIONS)
    assert RESOURCES[PERMANENT].edit_supports == ()
    assert RESOURCES[PERMANENT].supports_edit is False
    assert RESOURCES[DRAFT].read_supports == READ_ACTIONS
    assert RESOURCES[DRAFT].edit_supports == EDIT_ACTIONS
    assert RESOURCES[DRAFT].can_delete_file is False
    assert RESOURCES[STRUCTURED].write_mode == "read_only"
    assert RESOURCES[STRUCTURED].can_delete_file is False
    assert RESOURCES[WORLD].can_delete_file is True
    assert RESOURCES[WORLD].requires_path is True


def test_resolve_ids_to_runtime_data_dir(resolver: ResourceResolver, data_dir: Path):
    draft = resolver.resolve(DRAFT)
    assert draft.spec.id == DRAFT
    assert draft.path == data_dir / "resources" / "草稿本.md"
    world = resolver.resolve(WORLD)
    assert world.path == data_dir / "resources"


def test_unknown_id_lists_registered_ids(resolver: ResourceResolver):
    with pytest.raises(Exception, match="未知的资源 ID") as exc:
        resolver.resolve("secret")
    for resource in RESOURCES:
        assert resource in str(exc.value)


def test_relative_path_resolves_against_data_dir(resolver: ResourceResolver, data_dir: Path):
    resolved = resolver.resolve(None, "resources/笔记.md")
    assert resolved.spec.id == WORLD
    assert resolved.path == data_dir / "resources" / "笔记.md"


def test_absolute_registered_path_is_accepted(resolver: ResourceResolver, data_dir: Path):
    resolved = resolver.resolve(None, str(data_dir / "resources" / "草稿本.md"))
    assert resolved.spec.id == DRAFT


def test_path_outside_registered_resources_is_rejected(resolver: ResourceResolver, tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("秘密", encoding="utf-8")
    with pytest.raises(Exception, match="不在授权资源内"):
        resolver.resolve(None, str(outside))


def test_parent_traversal_is_rejected(resolver: ResourceResolver):
    with pytest.raises(Exception, match="穿越"):
        resolver.resolve(WORLD, "../memory/structured.md")


def test_world_path_to_draft_is_canonicalized(resolver: ResourceResolver):
    resolved = resolver.resolve(WORLD, "草稿本.md")
    assert resolved.spec.id == DRAFT


def test_world_path_to_structured_is_rejected_when_using_world_id(
    resolver: ResourceResolver, data_dir: Path
):
    with pytest.raises(Exception, match="不在授权资源内"):
        resolver.resolve(WORLD, str(data_dir / "memory" / "structured.md"))


def test_draft_rejects_other_paths(resolver: ResourceResolver):
    with pytest.raises(Exception, match="草稿本"):
        resolver.resolve(DRAFT, "笔记.md")


def test_permanent_is_virtual_and_rejects_paths(resolver: ResourceResolver):
    resolved = resolver.resolve(PERMANENT)
    assert resolved.path is None
    with pytest.raises(Exception, match="不支持路径"):
        resolver.resolve(PERMANENT, "anything")


def test_permission_matrix_is_enforced(resolver: ResourceResolver):
    permanent = resolver.resolve(PERMANENT)
    resolver.require(permanent, ACTION_READ)
    with pytest.raises(Exception, match="不支持"):
        resolver.require(permanent, ACTION_APPEND)

    draft = resolver.resolve(DRAFT)
    resolver.require(draft, ACTION_DELETE)
    assert draft.spec.can_delete_file is False


def test_symlink_escape_is_rejected(resolver: ResourceResolver, data_dir: Path, tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("外部", encoding="utf-8")
    link = data_dir / "resources" / "link.md"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")
    with pytest.raises(Exception, match="不在"):
        resolver.resolve(WORLD, "link.md")


def test_junction_escape_is_rejected(resolver: ResourceResolver, data_dir: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = data_dir / "resources" / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("当前环境无法创建目录联接")
    with pytest.raises(Exception, match="不在"):
        resolver.resolve(WORLD, "junction/new.md")


def test_symlink_inside_world_resolves_to_the_real_resource(
    resolver: ResourceResolver, data_dir: Path
):
    link = data_dir / "resources" / "draft-alias.md"
    try:
        os.symlink(data_dir / "resources" / "草稿本.md", link)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")
    resolved = resolver.resolve(WORLD, "draft-alias.md")
    assert resolved.spec.id == DRAFT


def test_edit_is_allowed_on_writable_resources(resolver: ResourceResolver):
    for resource_id in (DRAFT, WORLD):
        resolver.require(resolver.resolve(resource_id), ACTION_REPLACE_LINES)


def test_edit_is_rejected_on_read_only_structured(resolver: ResourceResolver):
    with pytest.raises(Exception, match="不支持 replace_lines"):
        resolver.require(resolver.resolve(STRUCTURED), ACTION_REPLACE_LINES)
