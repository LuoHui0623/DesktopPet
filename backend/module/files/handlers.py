"""资源处理器：把每次工具调用的产出收敛到按资源分派的适配器。

`FileService` 只保留"解析资源 → 鉴权 → 审批 → 结算"的公共流程，
具体如何索引、读取与检索由本模块的处理器决定；新增资源时只需注册一个处理器。
"""

import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta, tzinfo
from pathlib import Path
from typing import Protocol

from constants.files import (
    ACTION_APPEND,
    ACTION_DELETE,
    ACTION_GREP,
    ACTION_INDEX,
    ACTION_INSERT,
    ACTION_READ,
    ACTION_REPLACE_LINES,
    ACTION_REPLACE_REGEX,
    DRAFT,
    PERMANENT,
    RESOURCE_ORDER,
    RESOURCES,
    STRUCTURED,
    WORLD,
)
from infra.llm.tokens import TokenCounter, TokenCountError, create_counter
from module.agent.context import TaskContext
from module.agent.skills import SkillRegistry
from module.files import token as snapshot_token
from module.files.format import (
    day_text,
    display,
    file_display,
    is_link,
    moment,
    parse_moment,
    relative_path,
    render_permanent,
)
from module.files.resolver import FileError, Resource
from module.files.staging import Editor
from module.files.text import (
    grep_lines,
    grep_text,
    lines_of,
    numbered,
    read_text,
    require_file,
    text_state,
)
from module.files.text import read_lines as read_lines_state
from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService, render_entry_lines
from module.memory.memory_service import split_lines

STATUS_LINE_LIMIT = 500
GREP_LIMIT = 100

# 写入前是否必须先 read；草稿本与小世界走暂存层，虚拟资源不参与写入。
READ_BEFORE_EDIT = {
    DRAFT: True,
    STRUCTURED: False,
    PERMANENT: False,
    WORLD: True,
}

ACTION_DOCS = {
    ACTION_INDEX: ("列出资源、路径与权限；resource 为 world 时列出目录树，为 skill 时列出能力清单"),
    ACTION_READ: (
        "读取文本并附行号（从 1 开始）；line_ranges 为 [[起, 止], ...]；"
        "永久记忆用 date 或 from/to 指定时间范围"
    ),
    ACTION_GREP: (
        "按 Python 正则检索；world 未给 path 时递归检索目录；永久记忆用 date 或 from/to 限定范围"
    ),
    ACTION_REPLACE_LINES: (
        "用 content 整段替换 line_ranges 指定的行；expect 可给出目标行原文，用于冲突检测"
    ),
    ACTION_APPEND: "追加到文件末尾；world 中不存在的路径会新建文件",
    ACTION_INSERT: "在 line 之前插入 content，line 取 1 到 行数+1",
    ACTION_REPLACE_REGEX: (
        "在 line_ranges 范围内用正则匹配，把匹配文字替换为 content；范围必填，没有匹配时报错"
    ),
    ACTION_DELETE: (
        "删除 line_ranges 指定的行并保留文件；省略 line_ranges 时只有 world 能删除文件本体，"
        "草稿本不能删除文件"
    ),
}

PARAMETER_DOCS = {
    "resource": "资源 ID：draft、structured、permanent、world；index 与 read 还接受 skill",
    "path": "world 内的相对路径；draft 只接受自己的文件名或等价路径",
    "line_ranges": "行号位置 [[起, 止], ...]，从 1 开始并包含两端；按动作语义可省略",
    "content": "字面文本，不解析反斜杠转义；append / insert / replace_lines 不能为空",
    "expect": "可选冲突检测：目标行原文，可传字符串或与 line_ranges 等长的列表",
    "date": "永久记忆的日期，格式 YYYY-MM-DD",
    "from/to": "永久记忆的闭区间，ISO 8601；不带时区时按配置时区解释",
}

LIMIT_DOCS = (
    "grep 默认最多返回 100 条匹配并在截断时说明；永久记忆单次 read / grep 的结果"
    "不得超过子代理模型的 max_tokens，超限需缩小 date 或 from/to 范围"
)


@dataclass(frozen=True)
class ModelLimit:
    max_tokens: int
    tokenizer: str


class ReadLimiter:
    """永久记忆读取的 token 上限来源与计数器缓存。"""

    def __init__(
        self,
        limit_provider: Callable[[], ModelLimit],
        *,
        counter_factory: Callable[[str], TokenCounter] | None = None,
    ) -> None:
        self._limit_provider = limit_provider
        self._counter_factory = counter_factory or create_counter
        self._counters: dict[str, TokenCounter] = {}
        self._lock = threading.Lock()

    def limit(self) -> ModelLimit:
        limit = self._limit_provider()
        if limit.max_tokens <= 0:
            raise FileError("子代理模型的 max_tokens 必须大于 0")
        return limit

    def counter(self, tokenizer: str) -> TokenCounter:
        """按 tokenizer 缓存计数器，避免每次读取都重新加载编码表。"""
        with self._lock:
            counter = self._counters.get(tokenizer)
            if counter is None:
                counter = self._counter_factory(tokenizer)
                self._counters[tokenizer] = counter
            return counter

    def enforce(self, content: str, label: str) -> None:
        try:
            limit = self.limit()
        except Exception as exc:
            raise FileError(f"{label}无法确定单次读取上限：{exc}") from exc
        try:
            count = self.counter(limit.tokenizer).count(content)
        except TokenCountError as exc:
            raise FileError(f"{label}的 token 计数失败，无法确认读取上限：{exc}") from exc
        if count.tokens > limit.max_tokens:
            estimate = "估算值约 " if count.estimated else "约 "
            raise FileError(
                f"{label}结果需要{estimate}{count.tokens} token，"
                f"超过单次读取上限 {limit.max_tokens} token；请缩小时间范围或先用 grep 定位"
            )


@dataclass(frozen=True)
class Query:
    """一次读取或检索的参数；各资源按需取用，缺省值表示"未指定"。"""

    name: str | None = None
    line_ranges: Sequence[Sequence[int]] | None = None
    day: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    pattern: str = ""
    limit: int = GREP_LIMIT


@dataclass(frozen=True)
class HandlerDeps:
    """处理器需要的外部能力；顺序固定，装配时按名传参。"""

    entries: MemoryEntryService
    daily: DailyLogService
    skills: SkillRegistry
    editor: Editor
    limiter: ReadLimiter
    timezone: tzinfo
    context: Callable[[], TaskContext]


class Handler(Protocol):
    """资源处理器契约：索引返回结构化目录，读取与检索返回带行号的文本。"""

    resource: str

    def index(self, deps: HandlerDeps, resource: Resource, query: Query) -> dict[str, object]: ...

    def read(self, deps: HandlerDeps, resource: Resource, query: Query) -> str: ...

    def grep(self, deps: HandlerDeps, resource: Resource, query: Query, regex) -> str: ...


def capabilities(deps: HandlerDeps, resource_id: str) -> dict[str, object]:
    """按注册表顺序投影当前任务权限快照的交集，供 index 展示。"""
    spec = RESOURCES[resource_id]
    permissions = deps.context().permissions
    read_supports = list(permissions.read_supports_for(resource_id))
    edit_supports = list(permissions.edit_supports_for(resource_id))
    return {
        "read_supports": read_supports,
        "edit_supports": edit_supports,
        "supports_edit": bool(edit_supports),
        "write_mode": spec.write_mode,
        "requires_path": spec.requires_path,
        "read_required_before_edit": READ_BEFORE_EDIT[resource_id],
        "can_delete_file": permissions.can_delete_file(resource_id),
    }


def persona(deps: HandlerDeps) -> str:
    return deps.context().persona_id


def skill_catalog(deps: HandlerDeps) -> list[dict[str, str]]:
    return [{"name": item.name, "description": item.description} for item in deps.skills.index()]


def overview(deps: HandlerDeps) -> dict[str, object]:
    """工具无参数调用时的总览：列资源、权限、能力清单与动作说明。"""
    context = deps.context()
    return {
        "kind": "overview",
        "persona": context.persona_id,
        "resources": [
            {
                "id": resource_id,
                "label": spec.label,
                "path": spec.path,
                "description": spec.description,
                **capabilities(deps, resource_id),
            }
            for resource_id in RESOURCE_ORDER
            for spec in [RESOURCES[resource_id]]
            if context.permissions.allows(resource_id, ACTION_INDEX)
        ],
        "skills": skill_catalog(deps),
        "actions": ACTION_DOCS,
        "parameters": PARAMETER_DOCS,
        "limits": LIMIT_DOCS,
        "next": ACTION_READ,
    }


def staging_target(resource: Resource) -> tuple[str, str, str] | None:
    """可暂存资源在写路径上的三元标识：资源 ID、暂存键、展示名。"""
    if resource.path is None:
        return None
    if resource.spec.id == DRAFT:
        name = resource.path.name
        return DRAFT, f"{DRAFT}:{name}", name
    if resource.spec.id == WORLD:
        shown = relative_path(resource.path, resource.root)
        return WORLD, f"{WORLD}:{shown}", shown
    return None


def entry_lines(deps: HandlerDeps) -> list[str]:
    return render_entry_lines(deps.entries.all())


def permanent_lines(
    deps: HandlerDeps,
    *,
    day: str | None,
    start_at: str | None,
    end_at: str | None,
) -> list[str]:
    """按日期或时间闭区间取当前人设的记忆，统一渲染成单行后编号。"""
    name = persona(deps)
    if day is not None:
        if start_at is not None or end_at is not None:
            raise FileError("date 与 from/to 不能同时使用")
        views = deps.daily.day_views(day_text(day), persona_id=name)
        views = [view for view in views if view.persona_id == name]
        ordered = sorted(views, key=lambda view: (moment(view.created_at), view.id))
        label = day
    else:
        if start_at is None and end_at is None:
            raise FileError("读取永久记忆需要 date 或 from/to 指定时间范围")
        start = parse_moment(start_at, deps.timezone) if start_at else None
        end = parse_moment(end_at, deps.timezone) if end_at else None
        if start is not None and end is not None and start > end:
            raise FileError("from 不能晚于 to")
        date_from = (start - timedelta(days=1)).date().isoformat() if start else None
        date_to = (end + timedelta(days=1)).date().isoformat() if end else None
        views = deps.daily.search(date_from=date_from, date_to=date_to, persona_id=name)
        chosen = []
        for view in views:
            at = moment(view.created_at).astimezone(deps.timezone)
            if start is not None and at < start:
                continue
            if end is not None and at > end:
                continue
            chosen.append((at, view))
        chosen.sort(key=lambda item: (item[0], item[1].id))
        ordered = [view for _, view in chosen]
        label = f"{start_at or '最早'} 至 {end_at or '最晚'}"
    if not ordered:
        raise FileError(f"{label} 没有当前人设的对话记录")
    return [render_permanent(view, deps.timezone) for view in ordered]


class SkillHandler:
    """能力清单：只提供目录与说明书读取，不支持检索。"""

    resource = "skill"

    def index(self, deps: HandlerDeps, resource: Resource, query: Query) -> dict[str, object]:
        return {"kind": "skills", "skills": skill_catalog(deps), "next": ACTION_READ}

    def read(self, deps: HandlerDeps, resource: Resource, query: Query) -> str:
        if not query.name:
            raise FileError("读取技能说明书需要 name；先用 index（resource 为 skill）查看清单")
        return deps.skills.read(query.name)

    def grep(self, deps: HandlerDeps, resource: Resource, query: Query, regex) -> str:
        raise FileError("能力清单不支持检索")


class PermanentHandler:
    """永久记忆：只读，且受模型 max_tokens 约束。"""

    resource = PERMANENT

    def index(self, deps: HandlerDeps, resource: Resource, query: Query) -> dict[str, object]:
        try:
            limit = deps.limiter.limit()
            limit_payload: dict[str, object] = {
                "max_tokens": limit.max_tokens,
                "tokenizer": limit.tokenizer,
            }
        except Exception as exc:  # 模型不可用时仍允许查看索引
            limit_payload = {"max_tokens": None, "reason": str(exc)}
        return {
            "kind": "permanent",
            "resource": PERMANENT,
            "label": RESOURCES[PERMANENT].label,
            "persona": persona(deps),
            "dates": deps.daily.dates(),
            "read_only": True,
            **capabilities(deps, PERMANENT),
            "limit": limit_payload,
            "description": RESOURCES[PERMANENT].description,
            "next": ACTION_READ,
        }

    def read(self, deps: HandlerDeps, resource: Resource, query: Query) -> str:
        source = permanent_lines(deps, day=query.day, start_at=query.start_at, end_at=query.end_at)
        content = numbered(source, query.line_ranges)
        deps.limiter.enforce(content, "永久记忆读取")
        return content

    def grep(self, deps: HandlerDeps, resource: Resource, query: Query, regex) -> str:
        source = permanent_lines(deps, day=query.day, start_at=query.start_at, end_at=query.end_at)
        content = grep_lines(source, regex, query.line_ranges, query.limit)
        deps.limiter.enforce(content, "永久记忆检索")
        return content


class StructuredHandler:
    """结构化记忆：条目由服务维护，工具侧只读。"""

    resource = STRUCTURED

    def index(self, deps: HandlerDeps, resource: Resource, query: Query) -> dict[str, object]:
        enabled, disabled = deps.entries.counts()
        return {
            "kind": "structured",
            "resource": STRUCTURED,
            "label": RESOURCES[STRUCTURED].label,
            "entries": enabled + disabled,
            "enabled": enabled,
            "disabled": disabled,
            "tags": deps.entries.tags(),
            "read_only": True,
            **capabilities(deps, STRUCTURED),
            "description": RESOURCES[STRUCTURED].description,
            "next": ACTION_READ,
        }

    def read(self, deps: HandlerDeps, resource: Resource, query: Query) -> str:
        return numbered(entry_lines(deps), query.line_ranges)

    def grep(self, deps: HandlerDeps, resource: Resource, query: Query, regex) -> str:
        return grep_lines(entry_lines(deps), regex, query.line_ranges, query.limit)


class FileHandler:
    """真实文件：草稿本与小世界共用，区别只在暂存键与展示名。"""

    def __init__(self, resource: str = WORLD) -> None:
        self.resource = resource

    def index(self, deps: HandlerDeps, resource: Resource, query: Query) -> dict[str, object]:
        if resource.spec.id == WORLD and resource.path is not None and resource.path.is_dir():
            return _tree(deps, resource)
        return _file_index(deps, resource)

    def read(self, deps: HandlerDeps, resource: Resource, query: Query) -> str:
        assert resource.path is not None
        if resource.path.is_dir():
            raise FileError("目标是目录，请指定具体文件；用 index 查看目录内容")
        task = deps.context().main_task_id
        target = staging_target(resource)
        snapshot = deps.editor.snapshot(task, target[1]) if target is not None else None
        if snapshot is not None and snapshot.writes > 0:
            text = snapshot.change.staged_text
        else:
            require_file(resource.path)
            text = read_text(resource.path)
            if target is not None:
                resource_id, key, shown = target
                deps.editor.establish(
                    task,
                    resource=resource_id,
                    key=key,
                    display=shown,
                    abs_path=resource.path,
                    text=text,
                    created=False,
                )
                snapshot = deps.editor.snapshot(task, key)
        content = numbered(text_state(text)[0], query.line_ranges)
        if snapshot is not None:
            content = f"{content}\n\n{snapshot_token.token_line(snapshot.version)}"
        return content

    def grep(self, deps: HandlerDeps, resource: Resource, query: Query, regex) -> str:
        assert resource.path is not None
        if resource.path.is_dir():
            return _grep_tree(deps, resource, regex, query.limit)
        task = deps.context().main_task_id
        target = staging_target(resource)
        snapshot = deps.editor.snapshot(task, target[1]) if target is not None else None
        if snapshot is not None and snapshot.writes > 0:
            source = split_lines(snapshot.change.staged_text)
        else:
            try:
                source = lines_of(resource.path)
            except FileNotFoundError as exc:
                raise FileError(f"文件不存在：{resource.path.name}") from exc
            except UnicodeDecodeError as exc:
                raise FileError(f"不是 UTF-8 文本：{resource.path.name}") from exc
        content = grep_lines(source, regex, query.line_ranges, query.limit)
        if snapshot is not None:
            content = f"{content}\n\n{snapshot_token.token_line(snapshot.version)}"
        return content


def _file_index(deps: HandlerDeps, resource: Resource) -> dict[str, object]:
    assert resource.path is not None
    payload: dict[str, object] = {
        "kind": "file",
        "resource": resource.spec.id,
        "label": resource.spec.label,
        "path": file_display(resource),
        **capabilities(deps, resource.spec.id),
    }
    if not resource.path.exists():
        if resource.spec.id != WORLD:
            raise FileError(f"文件不存在：{resource.path.name}；请检查 index 返回的路径")
        # 小世界未见过的路径可以 append 新建，是唯一未读可写的例外。
        payload.update(
            {
                "exists": False,
                "can_create": True,
                "read_required_before_edit": False,
                "next": ACTION_APPEND,
            }
        )
        return payload
    source, _ = read_lines_state(resource.path)
    payload.update(
        {
            "exists": True,
            "lines": len(source),
            "size": resource.path.stat().st_size,
            "next": ACTION_READ,
        }
    )
    return payload


def _tree(deps: HandlerDeps, resource: Resource) -> dict[str, object]:
    assert resource.path is not None
    root = resource.path
    entries: list[dict[str, object]] = []
    truncated = False
    for current, directories, files in os.walk(root):
        directories[:] = [name for name in sorted(directories) if not is_link(Path(current) / name)]
        for name in sorted(directories):
            entries.append(
                {
                    "path": relative_path(Path(current) / name, root),
                    "type": "directory",
                    "size": None,
                }
            )
        for name in sorted(files):
            target = Path(current) / name
            if is_link(target):
                entries.append(
                    {
                        "path": relative_path(target, root),
                        "type": "link",
                        "size": target.stat().st_size,
                    }
                )
                continue
            entries.append(
                {
                    "path": relative_path(target, root),
                    "type": "file",
                    "size": target.stat().st_size,
                }
            )
        if len(entries) > STATUS_LINE_LIMIT:
            entries = entries[:STATUS_LINE_LIMIT]
            truncated = True
            break
    return {
        "kind": "directory",
        "resource": WORLD,
        "label": RESOURCES[WORLD].label,
        "path": display(resource),
        **capabilities(deps, WORLD),
        "entries": entries,
        "truncated": truncated,
        "next": ACTION_INDEX,
    }


def _grep_tree(deps: HandlerDeps, resource: Resource, regex, limit: int) -> str:
    assert resource.path is not None
    root = resource.path
    task = deps.context().main_task_id
    pending = {
        snapshot.abs_path: snapshot
        for snapshot in deps.editor.pending(task)
        if snapshot.change.resource == WORLD
    }
    matches: list[str] = []
    truncated = False
    visited: set[Path] = set()
    for current, directories, files in os.walk(root):
        directories[:] = [name for name in sorted(directories) if not is_link(Path(current) / name)]
        for name in sorted(files):
            target = Path(current) / name
            if is_link(target):
                continue
            visited.add(target)
            snapshot = pending.get(target)
            if snapshot is not None and snapshot.delete_file:
                continue
            if snapshot is not None and snapshot.writes > 0:
                source = split_lines(snapshot.change.staged_text)
            else:
                try:
                    source = lines_of(target)
                except (OSError, UnicodeDecodeError):
                    continue
            relative = relative_path(target, root)
            for number, line in enumerate(source, start=1):
                if regex.search(line):
                    matches.append(f"{relative}:{number}: {line}")
                    if len(matches) >= limit:
                        truncated = True
                        break
            if truncated:
                break
        if truncated:
            break
    # 新建但尚未落盘的文件也属于本任务的暂存视图。
    for target, snapshot in sorted(pending.items(), key=lambda item: str(item[0])):
        if truncated or target in visited or snapshot.delete_file:
            continue
        if not target.is_relative_to(root):
            continue
        relative = relative_path(target, root)
        for number, line in enumerate(split_lines(snapshot.change.staged_text), start=1):
            if regex.search(line):
                matches.append(f"{relative}:{number}: {line}")
                if len(matches) >= limit:
                    truncated = True
                    break
    return grep_text(matches, truncated)


ROUTES: dict[str, Handler] = {
    "skill": SkillHandler(),
    PERMANENT: PermanentHandler(),
    STRUCTURED: StructuredHandler(),
    DRAFT: FileHandler(DRAFT),
    WORLD: FileHandler(WORLD),
}
