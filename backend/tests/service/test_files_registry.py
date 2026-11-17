"""资源登记表校验：能力派生、动作合法性、领域归属与路径约束。"""

from pathlib import Path

import pytest

from agent.files.registry import ACTION_APPEND, ACTION_READ, EDIT_ACTIONS, READ_ACTIONS
from agent.resources import (
    DIR_RESOURCES,
    DOMAIN_FILES,
    DOMAIN_MEMORY,
    DRAFT,
    RESOURCES,
    STRUCTURED,
    WORLD,
    ResourceSpec,
    resources_of,
)


def spec(**overrides) -> ResourceSpec:
    values = {
        "id": DRAFT,
        "domain": DOMAIN_FILES,
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


def test_registry_rejects_unknown_domain_and_non_file_domain_directory():
    with pytest.raises(ValueError, match="领域不合法"):
        spec(domain="DATABASE")
    # 只有文件领域允许声明目录资源：目录是 index 的入参，记忆对象没有目录语义。
    with pytest.raises(ValueError, match="只允许登记在 FILES 领域"):
        spec(id="custom", domain=DOMAIN_MEMORY, directory=True, path="resources")


def test_registry_registers_domain_and_description_for_every_resource():
    for resource_id, item in RESOURCES.items():
        assert item.id == resource_id
        assert item.domain in (DOMAIN_FILES, DOMAIN_MEMORY)
        assert item.description.strip(), f"{resource_id} 缺少说明，index 无法介绍该对象"


def test_registry_projects_resources_by_domain():
    assert [item.id for item in resources_of(DOMAIN_FILES)] == [DRAFT, WORLD]
    assert [item.id for item in resources_of(DOMAIN_MEMORY)] == [STRUCTURED, "permanent"]
    assert resources_of("SKILLS") == ()
    # 可索引目录只取文件领域里声明为目录的资源，与资源登记同源。
    assert [item.id for item in DIR_RESOURCES] == [WORLD]
