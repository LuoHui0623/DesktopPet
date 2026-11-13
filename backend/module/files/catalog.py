"""索引目录的构造：把资源、权限与占用投影成两种视图。

只读视图，不做校验、不建基准、不写盘。条目权限一律由「`type` 的动作集 ∩ 任务权限」
派生，因此 `action_permissions` 恒为子集，不存在第二份定义。
"""

import os
from collections.abc import Sequence
from pathlib import Path

from constants.files import (
    DIR_ACTIONS,
    DIR_RESOURCE_ORDER,
    DRAFT,
    FILE_ACTIONS,
    RESOURCES,
    WORLD,
    ResourceSpec,
)
from module.files.guard import FileGuard
from module.files.permissions import TaskPermissions
from module.files.resolver import FileError, Resource
from module.files.views import (
    ActionSpec,
    DirEntries,
    DirEntry,
    FileEntry,
    FileItem,
    FileSupport,
    Support,
    action_spec,
    dir_path,
    permission_filter,
)

DEFAULT_SKIP = frozenset({"__pycache__"})


def file_support(permissions: TaskPermissions) -> FileSupport:
    """领域视图：一级目录的 `resource_id` 与两组动作清单。"""
    resources = [
        resource_id
        for resource_id in DIR_RESOURCE_ORDER
        if permissions.allows(resource_id, "index")
    ]
    actions = permissions.actions_for(WORLD)
    return FileSupport(
        Support(
            dir_resources=resources,
            dir_actions=[
                action_spec(action, with_path=True) for action in DIR_ACTIONS if action in actions
            ],
            file_actions=[
                action_spec(action, with_path=False) for action in FILE_ACTIONS if action in actions
            ],
        )
    )


def dir_entries(
    resource: Resource,
    permissions: TaskPermissions,
    guard: FileGuard,
    task_id: str,
) -> DirEntries:
    """目录视图：文件与子目录条目，全量返回、不截断。"""
    assert resource.path is not None
    root = resource.path
    if not root.exists():
        raise FileError(f"目录不存在：{root.name}")
    entries: list[FileEntry] = []
    for child in sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name)):
        if child.name in DEFAULT_SKIP:
            continue
        if child.is_symlink() and child.is_dir():
            continue
        spec = _spec_of(child, resource)
        relative = _relative(child, root)
        if child.is_dir():
            entries.append(
                DirEntry(
                    name=child.name,
                    path=dir_path(relative),
                    action_permissions=permission_filter(
                        DIR_ACTIONS, permissions.actions_for(spec.id), "dir"
                    ),
                )
            )
            continue
        entries.append(
            FileItem(
                name=child.name,
                path=relative,
                action_permissions=permission_filter(
                    FILE_ACTIONS, permissions.actions_for(spec.id), "file"
                ),
                editing_status=guard.status(child, task_id),
            )
        )
    return DirEntries(resource.spec.id, entries)


def entries_for_target(
    target: str,
    resolve_world: Path,
    permissions: TaskPermissions,
    guard: FileGuard,
    task_id: str,
) -> DirEntries:
    """把 `index` 的单一入参解析成目录视图；两类失败必须区分，不返回空结果。"""
    spec = RESOURCES.get(target)
    if spec is not None:
        if not spec.directory:
            raise FileError(
                f"{spec.label}不是可索引目录：它是文件或只读视图，请改用 read / grep；"
                f"可索引目录：{'、'.join(DIR_RESOURCE_ORDER)}"
            )
        root = Path(resolve_world)
        return dir_entries(Resource(spec, root, root), permissions, guard, task_id)
    candidate = Path(target.replace(os.sep, "/").rstrip("/"))
    if candidate.parts and _safe_relative(candidate):
        root = Path(resolve_world) / candidate
        if root.is_dir():
            world = RESOURCES[WORLD]
            return dir_entries(
                Resource(world, root, Path(resolve_world)), permissions, guard, task_id
            )
    raise FileError(
        f"未注册的目录：{target}；可索引目录：{'、'.join(DIR_RESOURCE_ORDER)}；"
        "领域清单请用 FILES、SKILLS、TOOLS、MEMORY"
    )


def skill_listing(catalog: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [dict(item) for item in catalog]


def tool_listing(permissions: TaskPermissions) -> list[dict[str, object]]:
    """占位入口：只给动作名，不给参数与调用前提，也不承诺与 `file_actions` 同源。"""
    return [
        {"action": spec.action, "desc": spec.desc}
        for spec in (action_spec(action, with_path=False) for action in FILE_ACTIONS)
        if spec.action in permissions.actions_for(WORLD)
    ]


def memory_listing(specs: Sequence[ResourceSpec]) -> list[dict[str, object]]:
    return [{"name": spec.id, "label": spec.label} for spec in specs]


def _spec_of(child: Path, resource: Resource) -> ResourceSpec:
    """小世界根下的草稿本按草稿本规则计算权限，不随所在目录放宽。"""
    if resource.spec.id == WORLD:
        draft = RESOURCES[DRAFT]
        if draft.path is not None and child.name == Path(draft.path).name:
            return draft
    return resource.spec


def _relative(child: Path, root: Path) -> str:
    return child.relative_to(root).as_posix()


def _safe_relative(candidate: Path) -> bool:
    return ".." not in candidate.parts and not candidate.is_absolute()


__all__ = [
    "ActionSpec",
    "dir_entries",
    "entries_for_target",
    "file_support",
    "memory_listing",
    "skill_listing",
    "tool_listing",
]
