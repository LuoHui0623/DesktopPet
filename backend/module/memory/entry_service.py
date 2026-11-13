"""条目服务：memory_structured 的唯一读写入口。

AI 只经 ``add / enable / disable`` 触达这里；``update / merge`` 留给设置页人工治理。
仓储函数只收已开启事务的连接，本模块负责事务边界、格式校验与敏感信息检查。
"""

import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from infra.store.db import connect, transaction
from module.memory.contracts import (
    MemoryEntry,
    MemoryEntryCreate,
    MemoryEntryUpdate,
    TagMergeResult,
    normalize_tag,
    validate_tag,
)
from module.memory.repository import (
    count_entries,
    entry_by_id,
    entry_rows,
    entry_rows_with_tag,
    insert_entry,
    set_entry_state,
    tag_values,
    touch_entry,
    update_entry,
)
from module.memory.sensitive import sensitive_reason

logger = logging.getLogger("pet")

# 无 tags 条目的兜底分区：手改库或历史脏数据降级为空 tags 时仍有可读落点。
UNCATEGORIZED = "未分类"


class MemoryEntryError(Exception):
    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AddResult:
    entry: MemoryEntry
    reused: bool
    new_tags: tuple[str, ...]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MemoryEntryService:
    def __init__(
        self,
        db_file: Path,
        *,
        now: Callable[[], str] | None = None,
    ) -> None:
        self._db_file = db_file
        self._now = now or now_iso

    def load(
        self,
        *,
        status: str | None = "enabled",
        tags_any: Sequence[str] = (),
        limit: int | None = None,
    ) -> list[MemoryEntry]:
        """读取条目；默认不设上限（全量注入不得静默截断，Q23）。

        需要分页时由调用方显式传 ``limit``，服务层不替调用方决定截多少。
        """
        conn = self._connect()
        try:
            return entry_rows(conn, status=status, tags_any=tags_any, limit=limit)
        finally:
            conn.close()

    def all(self) -> list[MemoryEntry]:
        return self.load(status=None, limit=None)

    def get(self, entry_id: int) -> MemoryEntry:
        conn = self._connect()
        try:
            return self._require(conn, entry_id)
        finally:
            conn.close()

    def add(self, payload: MemoryEntryCreate) -> AddResult:
        self._reject_sensitive(payload.content, payload.tags)
        now = self._now()
        conn = self._connect()
        try:
            with transaction(conn):
                resolved, new_tags = self._resolve_tags(conn, payload.tags)
                existing = self._reusable(conn, payload.content, resolved)
                if existing is not None:
                    touch_entry(conn, existing.id, now)
                    entry = entry_by_id(conn, existing.id)
                    assert entry is not None
                else:
                    entry_id = insert_entry(
                        conn,
                        content=payload.content,
                        tags=resolved,
                        priority=payload.priority,
                        source_ref=payload.source_ref,
                        created_at=now,
                        updated_at=now,
                    )
                    entry = entry_by_id(conn, entry_id)
                    assert entry is not None
            if not existing:
                for tag in new_tags:
                    logger.warning("记忆条目 #%s 新建 tag：%s", entry.id, tag)
            return AddResult(entry=entry, reused=existing is not None, new_tags=tuple(new_tags))
        finally:
            conn.close()

    def update(self, entry_id: int, payload: MemoryEntryUpdate) -> MemoryEntry:
        if payload.content is not None:
            self._reject_sensitive(payload.content, payload.tags or ())
        conn = self._connect()
        try:
            with transaction(conn):
                self._require(conn, entry_id)
                update_entry(
                    conn,
                    entry_id,
                    updated_at=self._now(),
                    content=payload.content,
                    tags=payload.tags,
                    priority=payload.priority,
                )
                return self._require(conn, entry_id)
        finally:
            conn.close()

    def enable(self, entry_id: int) -> MemoryEntry:
        return self._set_state(entry_id, "enabled", None)

    def disable(self, entry_id: int, reason: str) -> MemoryEntry:
        text = reason.strip()
        if not text:
            raise MemoryEntryError("弃用条目需要写明原因", code="invalid")
        return self._set_state(entry_id, "disabled", text)

    def merge(self, source: str, target: str) -> TagMergeResult:
        try:
            source_tag = validate_tag(source)
            target_tag = validate_tag(target)
        except ValueError as exc:
            raise MemoryEntryError(str(exc), code="invalid") from exc
        if normalize_tag(source_tag) == normalize_tag(target_tag):
            raise MemoryEntryError("合并的源与目标不能是同一个 tag", code="invalid")
        conn = self._connect()
        try:
            with transaction(conn):
                rows = entry_rows_with_tag(conn, source_tag)
                if not rows:
                    raise MemoryEntryError(
                        f"没有条目使用 tag {source_tag!r}，无法合并", code="not_found"
                    )
                target_existed = any(
                    normalize_tag(value) == normalize_tag(target_tag) for value in tag_values(conn)
                )
                now = self._now()
                for entry in rows:
                    update_entry(
                        conn,
                        entry.id,
                        updated_at=now,
                        tags=_replace_tag(entry.tags, source_tag, target_tag),
                    )
                return TagMergeResult(
                    source=source_tag,
                    target=target_tag,
                    affected=len(rows),
                    target_existed=target_existed,
                )
        finally:
            conn.close()

    def tags(self) -> list[str]:
        conn = self._connect()
        try:
            return tag_values(conn)
        finally:
            conn.close()

    def counts(self) -> tuple[int, int]:
        conn = self._connect()
        try:
            return (
                count_entries(conn, status="enabled"),
                count_entries(conn, status="disabled"),
            )
        finally:
            conn.close()

    def structured_text(self, *, include_disabled: bool = True) -> str:
        lines = render_entry_lines(self.all(), include_disabled=include_disabled)
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    def _set_state(self, entry_id: int, state: str, reason: str | None) -> MemoryEntry:
        conn = self._connect()
        try:
            with transaction(conn):
                self._require(conn, entry_id)
                set_entry_state(conn, entry_id, state=state, reason=reason, updated_at=self._now())
                return self._require(conn, entry_id)
        finally:
            conn.close()

    def _resolve_tags(
        self, conn: sqlite3.Connection, tags: Sequence[str]
    ) -> tuple[list[str], list[str]]:
        """归一化命中复用已有拼写，否则沿用新写法并记为待告警的新增。"""
        spellings = {normalize_tag(value): value for value in tag_values(conn)}
        resolved: list[str] = []
        new_tags: list[str] = []
        for tag in tags:
            key = normalize_tag(tag)
            existing = spellings.get(key)
            if existing is None:
                spellings[key] = tag
                resolved.append(tag)
                new_tags.append(tag)
            else:
                resolved.append(existing)
        return resolved, new_tags

    def _reusable(
        self, conn: sqlite3.Connection, content: str, tags: Sequence[str]
    ) -> MemoryEntry | None:
        wanted = {normalize_tag(tag) for tag in tags}
        for entry in entry_rows(conn, status="enabled", content=content):
            if wanted & {normalize_tag(tag) for tag in entry.tags}:
                return entry
        return None

    def _require(self, conn: sqlite3.Connection, entry_id: int) -> MemoryEntry:
        entry = entry_by_id(conn, entry_id)
        if entry is None:
            raise MemoryEntryError(f"条目 #{entry_id} 不存在", code="not_found")
        return entry

    def _reject_sensitive(self, content: str, tags: Sequence[str]) -> None:
        reason = sensitive_reason(" ".join([content, *tags]))
        if reason is not None:
            raise MemoryEntryError(f"拒绝写入：{reason}", code="sensitive")

    def _connect(self) -> sqlite3.Connection:
        return connect(self._db_file)


@dataclass
class _Branch:
    """有序 tags 树的一个节点；``first`` 是该路径首次出现的读入序号。"""

    tag: str
    first: int
    children: dict[str, "_Branch"] = field(default_factory=dict)
    values: list[tuple[int, MemoryEntry]] = field(default_factory=list)


def render_entry_lines(
    entries: Sequence[MemoryEntry], *, include_disabled: bool = True
) -> list[str]:
    """按有序 tags 组装 Markdown 树（第六节第 24 条），聊天注入与文件虚拟视图共用。

    一条记忆只对应一条有序路径：首个 tag 作 ``##`` 标题，其余 tag 用 ``\\t`` 分层；
    相同有序路径的正文集中展示，不同顺序按各自路径分别展示、不因顺序筛除。
    渲染只组织表述：不改写 ``content``、不丢路径语义，也不按重要性排序
    （同层顺序＝读入顺序，确定性可复现）。
    """
    roots: dict[str, _Branch] = {}
    for index, entry in enumerate(entries):
        if not include_disabled and entry.status_state == "disabled":
            continue
        path = tuple(tag for tag in entry.tags if tag) or (UNCATEGORIZED,)
        _branch_at(roots, path, index).values.append((index, entry))
    lines: list[str] = []
    for root in roots.values():
        lines.append(f"## {root.tag}")
        lines.extend(_render_root_body(root))
    return lines


def _branch_at(roots: dict[str, _Branch], path: Sequence[str], index: int) -> _Branch:
    """沿路径下钻并补齐缺失节点；同层子节点保持首次出现顺序。"""
    siblings = roots
    branch: _Branch | None = None
    for tag in path:
        branch = siblings.get(tag)
        if branch is None:
            branch = _Branch(tag=tag, first=index)
            siblings[tag] = branch
        siblings = branch.children
    assert branch is not None
    return branch


def _render_root_body(root: _Branch) -> list[str]:
    """一级标签的正文：有子路径时与子节点并列，无子路径时单值成行、多值成列表。"""
    if root.children:
        return _render_items(root, 0)
    labels = [_label(entry) for _, entry in root.values]
    if len(labels) == 1:
        return labels
    return [f"- {label}" for label in labels]


def _render_items(branch: _Branch, level: int) -> list[str]:
    """正文与下级 tag 按首次出现顺序合并，整体缩进 ``level`` 个制表符。"""
    pad = "\t" * level
    rows: list[tuple[int, list[str]]] = [
        (index, [f"{pad}- {_label(entry)}"]) for index, entry in branch.values
    ]
    rows.extend((child.first, _render_child(child, level)) for child in branch.children.values())
    rows.sort(key=lambda row: row[0])
    lines: list[str] = []
    for _, chunk in rows:
        lines.extend(chunk)
    return lines


def _render_child(child: _Branch, level: int) -> list[str]:
    """非一级标签：末级与正文同行加粗，有下级时自身独占一行并继续下钻。"""
    pad = "\t" * level
    if not child.children:
        labels = "、".join(_label(entry) for _, entry in child.values)
        return [f"{pad}- **{child.tag}**：{labels}"]
    return [f"{pad}- {child.tag}", *_render_items(child, level + 1)]


def _label(entry: MemoryEntry) -> str:
    """正文原样输出；停用条目只在人工视图里带标记，注入路径不会看到它。"""
    if entry.status_state != "disabled":
        return entry.content
    reason = f"（{entry.status_reason}）" if entry.status_reason else ""
    return f"{entry.content}[已弃用]{reason}"


def _replace_tag(tags: Sequence[str], source: str, target: str) -> list[str]:
    key = normalize_tag(source)
    updated: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = target if normalize_tag(tag) == key else tag
        normalized = normalize_tag(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        updated.append(value)
    return updated
