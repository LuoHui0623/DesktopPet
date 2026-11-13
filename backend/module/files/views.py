"""索引视图：两种并列结果与类型分层。

`Index` 只负责探索，结果类型只描述数据。领域视图回答「有哪些目录、能做什么」，
目录视图回答「目录里有什么」——两者是并列结果，不存在「同一套结构的两个变体」。
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, ClassVar, Literal

from constants.files import (
    ACTION_GROUP,
    DOMAIN_FILES,
    DOMAIN_MEMORY,
    DOMAIN_SKILLS,
    DOMAIN_TOOLS,
    normalize_domain,
)
from module.files.guard import EditingStatus
from module.files.schemas import derive_args, first_sentence


@dataclass(frozen=True)
class ActionSpec:
    action: str
    args: str
    desc: str


@dataclass(frozen=True)
class Support:
    dir_resources: list[str]
    dir_actions: list[ActionSpec]
    file_actions: list[ActionSpec]


@dataclass(frozen=True)
class FileSupport:
    support: Support


@dataclass(frozen=True, kw_only=True)
class FileEntry:
    """条目基类；`kw_only` 不可省，否则子类给 `type` 默认值后会触发参数顺序错误。"""

    type: Literal["dir", "file"]
    name: str
    path: str
    action_permissions: list[str]


@dataclass(frozen=True, kw_only=True)
class DirEntry(FileEntry):
    type: Literal["dir"] = "dir"


@dataclass(frozen=True, kw_only=True)
class FileItem(FileEntry):
    type: Literal["file"] = "file"
    editing_status: EditingStatus | None = None


@dataclass(frozen=True)
class DirEntries:
    resource_id: str
    entries: list[FileEntry]


@dataclass(frozen=True)
class Listing:
    """只承诺「返回清单」的领域；字段集合不在本域契约内。"""

    domain: str
    items: list[dict[str, Any]] = field(default_factory=list)


class Index:
    """探索行为的基类；子类各自固定一个领域，不读正文、不建立基准、不批准操作。"""

    domain: ClassVar[str]

    def explore(self, target: str) -> Any:
        raise NotImplementedError


class FileIndex(Index):
    domain: ClassVar[str] = DOMAIN_FILES

    def __init__(
        self,
        support: Callable[[], FileSupport],
        entries: Callable[[str], DirEntries],
    ) -> None:
        self._support = support
        self._entries = entries

    def explore(self, target: str) -> FileSupport | DirEntries:
        if normalize_domain(target) == DOMAIN_FILES:
            return self._support()
        return self._entries(target)


class SkillIndex(Index):
    domain: ClassVar[str] = DOMAIN_SKILLS

    def __init__(self, catalog: Callable[[], list[dict[str, Any]]]) -> None:
        self._catalog = catalog

    def explore(self, target: str) -> Listing:
        return Listing(self.domain, self._catalog())


class ToolIndex(Index):
    """占位入口：形态不定义，也不承诺与 `file_actions` 同源。"""

    domain: ClassVar[str] = DOMAIN_TOOLS

    def __init__(self, catalog: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        self._catalog = catalog

    def explore(self, target: str) -> Listing:
        return Listing(self.domain, self._catalog() if self._catalog else [])


class MemoryIndex(Index):
    """只给名称，不展示分区、条目与条目 ID。"""

    domain: ClassVar[str] = DOMAIN_MEMORY

    def __init__(self, catalog: Callable[[], list[dict[str, Any]]]) -> None:
        self._catalog = catalog

    def explore(self, target: str) -> Listing:
        return Listing(self.domain, self._catalog())


def action_spec(action: str, *, with_path: bool) -> ActionSpec:
    return ActionSpec(
        action=action,
        args=derive_args(action, with_path=with_path),
        desc=first_sentence(action),
    )


def group_actions(actions: tuple[str, ...], *, group: str) -> list[ActionSpec]:
    return [action_spec(action, with_path=group == "dir") for action in actions]


def dir_path(relative: str) -> str:
    """目录条目的 `path` 带尾分隔符，可直接回传给 `index`、`read` 与写动作。"""
    cleaned = relative.replace("/", os.sep).rstrip(os.sep)
    return f"{cleaned}{os.sep}"


def entry_group(entry_type: str) -> str:
    return "dir" if entry_type == "dir" else "file"


def permission_filter(
    actions: tuple[str, ...], allowed: frozenset[str], entry_type: str | None = None
) -> list[str]:
    """按条目 `type` 的动作集过滤权限，结果恒为子集、来源唯一。"""
    chosen = (
        actions
        if entry_type is None
        else tuple(action for action in actions if ACTION_GROUP[action] == entry_group(entry_type))
    )
    return [action for action in chosen if action in allowed]


def payload(view: Any) -> Any:
    """序列化：`None` 字段直接省略，不输出 `null`；`type` 以字符串输出。"""
    if is_dataclass(view) and not isinstance(view, type):
        result: dict[str, Any] = {}
        for item in fields(view):
            value = getattr(view, item.name)
            if value is None:
                continue
            result[item.name] = payload(value)
        return result
    if isinstance(view, list | tuple):
        return [payload(item) for item in view]
    return view
