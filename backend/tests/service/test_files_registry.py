from pathlib import Path

import pytest

from constants.files import (
    ACTION_APPEND,
    ACTION_READ,
    DRAFT,
    EDIT_ACTIONS,
    READ_ACTIONS,
    WORLD,
    ResourceSpec,
)


def spec(**overrides) -> ResourceSpec:
    values = {
        "id": DRAFT,
        "label": "草稿本",
        "path": "resources/草稿本.md",
        "read_supports": READ_ACTIONS,
        "edit_supports": EDIT_ACTIONS,
        "write_mode": "staged",
        "requires_path": False,
        "can_delete_file": False,
        "description": "",
    }
    values.update(overrides)
    return ResourceSpec(**values)


def test_registry_derives_actions_and_supports_edit_from_the_two_tuples():
    item = spec()
    assert item.actions == frozenset(READ_ACTIONS) | frozenset(EDIT_ACTIONS)
    assert item.supports_edit is True
    readonly = spec(id="custom", edit_supports=(), write_mode="read_only")
    assert readonly.supports_edit is False
    assert readonly.actions == frozenset(READ_ACTIONS)


def test_registry_rejects_unknown_or_misplaced_actions():
    with pytest.raises(ValueError, match="未知读动作"):
        spec(read_supports=(ACTION_READ, "peek"))
    with pytest.raises(ValueError, match="未知写动作"):
        spec(edit_supports=(ACTION_APPEND, "rewrite"))
    with pytest.raises(ValueError, match="未知写动作"):
        spec(edit_supports=(ACTION_READ,))
    with pytest.raises(ValueError, match="未知读动作"):
        spec(read_supports=(ACTION_APPEND,))


def test_registry_rejects_duplicates():
    with pytest.raises(ValueError, match="read_supports 有重复"):
        spec(read_supports=(ACTION_READ, ACTION_READ))
    with pytest.raises(ValueError, match="edit_supports 有重复"):
        spec(edit_supports=(ACTION_APPEND, ACTION_APPEND))


def test_registry_enforces_write_mode_consistency():
    with pytest.raises(ValueError, match="只读资源"):
        spec(id="custom", edit_supports=EDIT_ACTIONS, write_mode="read_only")
    with pytest.raises(ValueError, match="write_mode 必须是 staged"):
        spec(edit_supports=(), write_mode="read_only")
    with pytest.raises(ValueError, match="write_mode 不合法"):
        spec(write_mode="immediate")
    with pytest.raises(ValueError, match="write_mode 不合法"):
        spec(write_mode="eventual")


def test_registry_only_allows_world_to_delete_file_bodies():
    with pytest.raises(ValueError, match="只有 world"):
        spec(can_delete_file=True)


def test_registry_requires_relative_paths():
    with pytest.raises(ValueError, match="相对数据目录"):
        spec(path=str(Path(__file__).resolve()))
    world = spec(id=WORLD, path="resources", requires_path=True, can_delete_file=True)
    assert world.can_delete_file is True
