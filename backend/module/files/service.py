"""统一文件访问服务：八个动作统一按资源权限执行。"""

import json
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path

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
from infra.files import io
from infra.llm.tokens import TokenCounter, TokenCountError, create_counter
from module.agent.approval import ReadApprovalRequest, ReadApprover
from module.agent.context import SOURCE_USER, TaskContext, TaskContextError, current_task
from module.agent.skills import SkillRegistry
from module.files.resolver import FileDenied, FileError, Resource, ResourceResolver
from module.files.staging import Editor, StagedSnapshot
from module.memory.daily_log_service import DailyLogService
from module.memory.index import structured_index
from module.memory.memory_service import (
    MemoryReadError,
    MemoryWriter,
    StructuredMemoryDoc,
    checked_ranges,
    compile_pattern,
    expect_matches,
    number_lines,
    parse_ranges,
    replace_ranges,
    section,
    split_lines,
)

STATUS_LINE_LIMIT = 500
GREP_LIMIT = 100

# 写入前是否必须先 read；structured 即时写入且不建草稿 base，是显式例外。
_READ_BEFORE_EDIT = {
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
        "草稿本与结构化记忆不能删除文件"
    ),
}

PARAMETER_DOCS = {
    "resource": "资源 ID：draft、structured、permanent、world；index 与 read 还接受 skill",
    "path": "world 内的相对路径；draft / structured 只接受自己的文件名或等价路径",
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


def _system_now(zone: tzinfo) -> datetime:
    return datetime.now(zone)


class FileService:
    def __init__(
        self,
        resolver: ResourceResolver,
        structured: StructuredMemoryDoc,
        daily: DailyLogService,
        skills: SkillRegistry,
        *,
        editor: Editor,
        read_gate: ReadApprover | None = None,
        timezone: tzinfo,
        limit_provider: Callable[[], ModelLimit],
        clock: Callable[[tzinfo], datetime] | None = None,
        counter_factory: Callable[[str], TokenCounter] | None = None,
    ) -> None:
        self._resolver = resolver
        self._structured = structured
        self._daily = daily
        self._skills = skills
        self._editor = editor
        self._read_gate = read_gate
        self._timezone = timezone
        self._clock = clock or _system_now
        self._limit_provider = limit_provider
        self._counter_factory = counter_factory or create_counter
        self._counters: dict[str, TokenCounter] = {}
        self._counter_lock = threading.Lock()

    @property
    def editor(self) -> Editor:
        return self._editor

    def index(self, resource: str | None = None, path: str | None = None) -> str:
        if resource == "skill":
            if path is not None:
                raise FileError("技能清单不需要 path")
            self._approve_read(
                action=ACTION_INDEX,
                topic="读取能力清单",
                task="index：列出可用能力",
                parameters="无额外参数",
                resource="skill",
            )
            receipts = self._settle_active(ACTION_INDEX, resource, None)
            return _json(_with_settlement(self._skill_index(), receipts))
        if resource is None and path is None:
            self._approve_read(
                action=ACTION_INDEX,
                topic="读取资源总览",
                task="index：查看资源、权限与下一步提示",
                parameters="无额外参数",
            )
            receipts = self._settle_active(ACTION_INDEX, resource, None)
            return _json(_with_settlement(self._overview(), receipts))
        resolved = self._resolver.resolve(resource, path)
        self._authorize(resolved, ACTION_INDEX)
        target = _approval_target(resolved)
        self._approve_read(
            action=ACTION_INDEX,
            topic=f"查看：{target}",
            task=f"index {target}",
            parameters=f"path={path}" if path else "无额外参数",
            resource=resolved.spec.id,
            path=_approval_path(resolved),
        )
        receipts = self._settle_active(ACTION_INDEX, resource, None)
        if resolved.spec.id == PERMANENT:
            return _json(_with_settlement(self._permanent_index(), receipts))
        assert resolved.path is not None
        if resolved.path.is_dir():
            return _json(_with_settlement(self._tree(resolved), receipts))
        return _json(_with_settlement(self._file_index(resolved), receipts))

    def read(
        self,
        resource: str | None = None,
        path: str | None = None,
        name: str | None = None,
        line_ranges: Sequence[Sequence[int]] | None = None,
        day: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
    ) -> str:
        if resource == "skill":
            if not name:
                raise FileError("读取技能说明书需要 name；先用 index（resource 为 skill）查看清单")
            self._approve_read(
                action=ACTION_READ,
                topic=f"读取能力：{name}",
                task=f"read skill {name}",
                parameters=f"name={name}",
                resource="skill",
                path=name,
            )
            receipts = self._settle_active(ACTION_READ, resource, None)
            return self._skills.read(name)
        resolved = self._resolver.resolve(resource, path)
        self._authorize(resolved, ACTION_READ)
        if resolved.spec.id != PERMANENT and resolved.path is not None and resolved.path.is_dir():
            raise FileError("目标是目录，请指定具体文件；用 index 查看目录内容")
        target = _approval_target(resolved)
        self._approve_read(
            action=ACTION_READ,
            topic=f"读取：{target}",
            task=f"read {target}",
            parameters=_read_parameters(line_ranges, day, start_at, end_at),
            resource=resolved.spec.id,
            path=_approval_path(resolved),
        )
        receipts = self._settle_active(ACTION_READ, resource, None)
        if resolved.spec.id == PERMANENT:
            content = self._read_permanent(line_ranges, day=day, start_at=start_at, end_at=end_at)
            return _with_receipts(content, receipts)
        return self._read_file(resolved, line_ranges, receipts)

    def grep(
        self,
        pattern: str,
        resource: str | None = None,
        path: str | None = None,
        line_ranges: Sequence[Sequence[int]] | None = None,
        day: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int = GREP_LIMIT,
    ) -> str:
        regex = compile_pattern(pattern)
        resolved = self._resolver.resolve(resource, path)
        self._authorize(resolved, ACTION_GREP)
        target = _approval_target(resolved)
        self._approve_read(
            action=ACTION_GREP,
            topic=f"检索：{target}",
            task=f"grep {target}",
            parameters=_grep_parameters(pattern, line_ranges, day, start_at, end_at, limit),
            resource=resolved.spec.id,
            path=_approval_path(resolved),
        )
        receipts = self._settle_active(ACTION_GREP, resource, None)
        if resolved.spec.id == PERMANENT:
            source = self._permanent_lines(day=day, start_at=start_at, end_at=end_at)
            content = _grep_lines(source, regex, line_ranges, limit)
            self._enforce_limit(content, "永久记忆检索")
            return _with_receipts(content, receipts)
        assert resolved.path is not None
        if resolved.path.is_dir():
            content = self._grep_tree(resolved, regex, limit)
            return _with_receipts(content, receipts)
        context = self._context()
        target = self._staging_target(resolved)
        snapshot = (
            self._editor.snapshot(context.main_task_id, target[1]) if target is not None else None
        )
        if snapshot is not None and snapshot.writes > 0:
            source = split_lines(snapshot.change.staged_text)
        else:
            try:
                source = _lines_of(resolved.path)
            except FileNotFoundError as exc:
                raise FileError(f"文件不存在：{resolved.path.name}") from exc
            except UnicodeDecodeError as exc:
                raise FileError(f"不是 UTF-8 文本：{resolved.path.name}") from exc
        return _with_receipts(_grep_lines(source, regex, line_ranges, limit), receipts)

    def append(self, resource: str, content: str, path: str | None = None) -> str:
        resolved = self._writable(resource, path, ACTION_APPEND)
        block = _content_block(content)
        if resolved.spec.id == STRUCTURED:
            receipts = self._settle_active(ACTION_APPEND, resource, None)
            start, end = self._writer().append(content)
            return _with_receipts(f"已追加，位于第 {start}-{end} 行", receipts)
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定文件")
        context = self._context()
        target = self._staging_target(resolved)
        assert target is not None
        resource_id, key, display = target
        receipts = list(
            self._editor.before_access(
                context.main_task_id, context.subtask_id, ACTION_APPEND, resource_id, key
            )
        )
        with self._editor.lock_for(resolved.path):
            snapshot = self._editor.snapshot(context.main_task_id, key)
            if snapshot is None:
                if resolved.spec.id == WORLD and not resolved.path.exists():
                    self._editor.establish(
                        context.main_task_id,
                        resource=resource_id,
                        key=key,
                        display=display,
                        abs_path=resolved.path,
                        text="",
                        created=True,
                    )
                    snapshot = self._editor.snapshot(context.main_task_id, key)
                else:
                    raise FileError(f"尚未读取 {display}；请先 read 再编辑")
            assert snapshot is not None
            if snapshot.delete_file:
                raise FileError(f"{display} 已暂存为删除，不能再编辑")
            source, trailing = _text_state(snapshot.change.staged_text)
            if snapshot.created and not source:
                trailing = True
            lines = [*source, *block]
            start = len(lines) - len(block) + 1
            self._editor.record(
                context.main_task_id,
                context.subtask_id,
                key,
                ACTION_APPEND,
                _join_text(lines, trailing),
                False,
            )
        receipts.extend(self._editor.after_write(context.main_task_id, context.subtask_id, key))
        message = f"已暂存，尚未写入文件；已追加至 {display} 第 {start}-{start + len(block) - 1} 行"
        return _with_receipts(message, receipts)

    def insert(self, resource: str, line: int, content: str, path: str | None = None) -> str:
        resolved = self._writable(resource, path, ACTION_INSERT)
        block = _content_block(content)
        if resolved.spec.id == STRUCTURED:
            receipts = self._settle_active(ACTION_INSERT, resource, None)
            start, end = self._writer().insert(line, content)
            return _with_receipts(f"已插入，位于第 {start}-{end} 行", receipts)
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定文件")
        context = self._context()
        target = self._staging_target(resolved)
        assert target is not None
        resource_id, key, display = target
        receipts = list(
            self._editor.before_access(
                context.main_task_id, context.subtask_id, ACTION_INSERT, resource_id, key
            )
        )
        with self._editor.lock_for(resolved.path):
            snapshot = self._require_staged(context.main_task_id, key, display)
            if snapshot.delete_file:
                raise FileError(f"{display} 已暂存为删除，不能再编辑")
            source, trailing = _text_state(snapshot.change.staged_text)
            if not 1 <= line <= len(source) + 1:
                raise FileError(f"插入位置 {line} 超出范围：应为 1–{len(source) + 1}")
            lines = [*source[: line - 1], *block, *source[line - 1 :]]
            self._editor.record(
                context.main_task_id,
                context.subtask_id,
                key,
                ACTION_INSERT,
                _join_text(lines, trailing),
                False,
            )
        receipts.extend(self._editor.after_write(context.main_task_id, context.subtask_id, key))
        message = f"已暂存，尚未写入文件；已插入 {display} 第 {line}-{line + len(block) - 1} 行"
        return _with_receipts(message, receipts)

    def edit(
        self,
        resource: str,
        line_ranges: Sequence[Sequence[int]],
        content: str,
        path: str | None = None,
        expect: str | Sequence[str] | None = None,
    ) -> str:
        if not line_ranges:
            raise FileError("整段替换需要提供行号范围 line_ranges；删除整段内容请用 delete")
        resolved = self._writable(resource, path, ACTION_REPLACE_LINES)
        if resolved.spec.id == STRUCTURED:
            receipts = self._settle_active(ACTION_REPLACE_LINES, resource, None)
            start, end = self._writer().edit(line_ranges, content, expect)
            return _with_receipts(f"已编辑，位于第 {start}-{end} 行", receipts)
        block = _content_block(content)
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定文件")
        context = self._context()
        target = self._staging_target(resolved)
        assert target is not None
        resource_id, key, display = target
        receipts = list(
            self._editor.before_access(
                context.main_task_id, context.subtask_id, ACTION_REPLACE_LINES, resource_id, key
            )
        )
        with self._editor.lock_for(resolved.path):
            snapshot = self._require_staged(context.main_task_id, key, display)
            if snapshot.delete_file:
                raise FileError(f"{display} 已暂存为删除，不能再编辑")
            source, trailing = _text_state(snapshot.change.staged_text)
            ranges = _checked_ranges(source, line_ranges)
            expect_matches(source, ranges, expect)
            updated = replace_ranges(source, ranges, block)
            self._editor.record(
                context.main_task_id,
                context.subtask_id,
                key,
                ACTION_REPLACE_LINES,
                _join_text(updated, trailing),
                False,
            )
        receipts.extend(self._editor.after_write(context.main_task_id, context.subtask_id, key))
        shift = sum(len(block) - (end - start + 1) for start, end in ranges[:-1])
        message = (
            f"已暂存，尚未写入文件；已编辑 {display} 第 "
            f"{ranges[0][0]}-{ranges[-1][0] + shift + len(block) - 1} 行"
        )
        return _with_receipts(message, receipts)

    def replace(
        self,
        resource: str,
        line_ranges: Sequence[Sequence[int]],
        pattern: str,
        content: str,
        path: str | None = None,
    ) -> str:
        if not line_ranges:
            raise FileError("正则替换需要提供行号范围 line_ranges；整段替换请用 replace_lines")
        resolved = self._writable(resource, path, ACTION_REPLACE_REGEX)
        regex = compile_pattern(pattern)
        replacement = "\n".join(_content_block(content))
        if resolved.spec.id == STRUCTURED:
            receipts = self._settle_active(ACTION_REPLACE_REGEX, resource, None)
            start, end = self._writer().replace_pattern(line_ranges, pattern, content)
            return _with_receipts(f"已替换，位于第 {start}-{end} 行", receipts)
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定文件")
        context = self._context()
        target = self._staging_target(resolved)
        assert target is not None
        resource_id, key, display = target
        receipts = list(
            self._editor.before_access(
                context.main_task_id, context.subtask_id, ACTION_REPLACE_REGEX, resource_id, key
            )
        )
        with self._editor.lock_for(resolved.path):
            snapshot = self._require_staged(context.main_task_id, key, display)
            if snapshot.delete_file:
                raise FileError(f"{display} 已暂存为删除，不能再编辑")
            source, trailing = _text_state(snapshot.change.staged_text)
            ranges = _checked_ranges(source, line_ranges)
            updated, hits, first, last = _replace_ranges(source, ranges, regex, replacement)
            if not hits:
                raise FileError(f"正则 {pattern!r} 没有匹配到内容，请先用 read 确认原文")
            self._editor.record(
                context.main_task_id,
                context.subtask_id,
                key,
                ACTION_REPLACE_REGEX,
                _join_text(updated, trailing),
                False,
            )
        receipts.extend(self._editor.after_write(context.main_task_id, context.subtask_id, key))
        message = f"已暂存，尚未写入文件；已替换 {display} 共 {hits} 处，位于第 {first}-{last} 行"
        return _with_receipts(message, receipts)

    def delete(
        self,
        resource: str,
        path: str | None = None,
        line_ranges: Sequence[Sequence[int]] | None = None,
        expect: str | Sequence[str] | None = None,
    ) -> str:
        resolved = self._writable(resource, path, ACTION_DELETE)
        if resolved.spec.id == STRUCTURED:
            receipts = self._settle_active(ACTION_DELETE, resource, None)
            if not line_ranges:
                raise FileError("结构化记忆不能删除文件本体；请提供 line_ranges 删除内容")
            self._writer().delete(line_ranges, expect)
            return _with_receipts(f"已删除第 {_span_text(line_ranges)} 行", receipts)
        assert resolved.path is not None
        if resolved.path.is_dir():
            if resolved.spec.id == WORLD and line_ranges is None:
                raise FileError("不能删除小世界目录；只允许删除文件")
            raise FileError("目标是目录，请指定文件")
        if line_ranges is None and not self._context().permissions.can_delete_file(
            resolved.spec.id
        ):
            raise FileDenied(
                f"{resolved.spec.label}不能删除文件本体；请提供 line_ranges 只删除内容"
            )
        context = self._context()
        target = self._staging_target(resolved)
        assert target is not None
        resource_id, key, display = target
        receipts = list(
            self._editor.before_access(
                context.main_task_id, context.subtask_id, ACTION_DELETE, resource_id, key
            )
        )
        if line_ranges is None:
            with self._editor.lock_for(resolved.path):
                snapshot = self._require_staged(context.main_task_id, key, display)
                if snapshot.delete_file:
                    raise FileError(f"{display} 已暂存为删除")
                self._editor.record(
                    context.main_task_id,
                    context.subtask_id,
                    key,
                    "delete_file",
                    "",
                    True,
                )
            receipts.extend(self._editor.after_write(context.main_task_id, context.subtask_id, key))
            return _with_receipts(f"已暂存，尚未删除文件；已标记删除 {display}", receipts)
        with self._editor.lock_for(resolved.path):
            snapshot = self._require_staged(context.main_task_id, key, display)
            if snapshot.delete_file:
                raise FileError(f"{display} 已暂存为删除，不能再编辑")
            source, trailing = _text_state(snapshot.change.staged_text)
            ranges = _checked_ranges(source, line_ranges)
            expect_matches(source, ranges, expect)
            for start, end in reversed(ranges):
                del source[start - 1 : end]
            self._editor.record(
                context.main_task_id,
                context.subtask_id,
                key,
                ACTION_DELETE,
                _join_text(source, trailing),
                False,
            )
        receipts.extend(self._editor.after_write(context.main_task_id, context.subtask_id, key))
        return _with_receipts(
            f"已暂存，尚未写入文件；已删除 {display} 第 {_span_text(line_ranges)} 行", receipts
        )

    def _overview(self) -> dict[str, object]:
        context = self._context()
        return {
            "kind": "overview",
            "persona": context.persona_id,
            "resources": [
                {
                    "id": resource_id,
                    "label": spec.label,
                    "path": spec.path,
                    "description": spec.description,
                    **self._capabilities(resource_id),
                }
                for resource_id in RESOURCE_ORDER
                for spec in [RESOURCES[resource_id]]
                if context.permissions.allows(resource_id, ACTION_INDEX)
            ],
            "skills": self._skill_catalog(),
            "actions": ACTION_DOCS,
            "parameters": PARAMETER_DOCS,
            "limits": LIMIT_DOCS,
            "next": ACTION_READ,
        }

    def _capabilities(self, resource_id: str) -> dict[str, object]:
        """按注册表顺序投影当前任务权限快照的交集，供 index 展示。"""
        spec = RESOURCES[resource_id]
        permissions = self._context().permissions
        read_supports = list(permissions.read_supports_for(resource_id))
        edit_supports = list(permissions.edit_supports_for(resource_id))
        return {
            "read_supports": read_supports,
            "edit_supports": edit_supports,
            "supports_edit": bool(edit_supports),
            "write_mode": spec.write_mode,
            "requires_path": spec.requires_path,
            "read_required_before_edit": _READ_BEFORE_EDIT[resource_id],
            "can_delete_file": permissions.can_delete_file(resource_id),
        }

    def _skill_catalog(self) -> list[dict[str, str]]:
        return [
            {"name": item.name, "description": item.description} for item in self._skills.index()
        ]

    def _skill_index(self) -> dict[str, object]:
        return {"kind": "skills", "skills": self._skill_catalog(), "next": ACTION_READ}

    def _permanent_index(self) -> dict[str, object]:
        try:
            limit = self._limit()
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
            "persona": self._persona(),
            "dates": self._daily.dates(),
            "read_only": True,
            **self._capabilities(PERMANENT),
            "limit": limit_payload,
            "description": RESOURCES[PERMANENT].description,
            "next": ACTION_READ,
        }

    def _file_index(self, resource: Resource) -> dict[str, object]:
        assert resource.path is not None
        capabilities = self._capabilities(resource.spec.id)
        payload: dict[str, object] = {
            "kind": "file",
            "resource": resource.spec.id,
            "label": resource.spec.label,
            "path": _file_display(resource),
            **capabilities,
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
        source, _ = _read_lines(resource.path)
        payload.update(
            {
                "exists": True,
                "lines": len(source),
                "size": resource.path.stat().st_size,
                "next": ACTION_READ,
            }
        )
        if resource.spec.id == STRUCTURED:
            payload["headings"] = structured_index(source)["headings"]
        return payload

    def _tree(self, resource: Resource) -> dict[str, object]:
        assert resource.path is not None
        root = resource.path
        entries: list[dict[str, object]] = []
        truncated = False
        for current, directories, files in os.walk(root):
            directories[:] = [
                name for name in sorted(directories) if not _is_link(Path(current) / name)
            ]
            for name in sorted(directories):
                entries.append(
                    {
                        "path": _relative(Path(current) / name, root),
                        "type": "directory",
                        "size": None,
                    }
                )
            for name in sorted(files):
                target = Path(current) / name
                if _is_link(target):
                    entries.append(
                        {
                            "path": _relative(target, root),
                            "type": "link",
                            "size": target.stat().st_size,
                        }
                    )
                    continue
                entries.append(
                    {
                        "path": _relative(target, root),
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
            "path": _display(resource),
            **self._capabilities(WORLD),
            "entries": entries,
            "truncated": truncated,
            "next": ACTION_INDEX,
        }

    def _read_file(
        self,
        resource: Resource,
        line_ranges: Sequence[Sequence[int]] | None,
        receipts: Sequence[str] = (),
    ) -> str:
        assert resource.path is not None
        if resource.path.is_dir():
            raise FileError("目标是目录，请指定具体文件；用 index 查看目录内容")
        context = self._context()
        target = self._staging_target(resource)
        snapshot = (
            self._editor.snapshot(context.main_task_id, target[1]) if target is not None else None
        )
        if snapshot is not None and snapshot.writes > 0:
            text = snapshot.change.staged_text
        else:
            _require_file(resource.path)
            text = _read_text(resource.path)
            if target is not None:
                resource_id, key, display = target
                self._editor.establish(
                    context.main_task_id,
                    resource=resource_id,
                    key=key,
                    display=display,
                    abs_path=resource.path,
                    text=text,
                    created=False,
                )
        source = _text_state(text)[0]
        try:
            content = section(source, line_ranges) if line_ranges else number_lines(source)
        except MemoryReadError as exc:
            raise FileError(str(exc)) from exc
        return _with_receipts(content, receipts)

    def _read_permanent(
        self,
        line_ranges: Sequence[Sequence[int]] | None,
        *,
        day: str | None,
        start_at: str | None,
        end_at: str | None,
    ) -> str:
        source = self._permanent_lines(day=day, start_at=start_at, end_at=end_at)
        try:
            content = section(source, line_ranges) if line_ranges else number_lines(source)
        except MemoryReadError as exc:
            raise FileError(str(exc)) from exc
        self._enforce_limit(content, "永久记忆读取")
        return content

    def _permanent_lines(
        self,
        *,
        day: str | None,
        start_at: str | None,
        end_at: str | None,
    ) -> list[str]:
        persona = self._persona()
        if day is not None:
            if start_at is not None or end_at is not None:
                raise FileError("date 与 from/to 不能同时使用")
            views = self._daily.day_views(_day_text(day), persona_id=persona)
            views = [view for view in views if view.persona_id == persona]
            ordered = sorted(views, key=lambda view: (_moment(view.created_at), view.id))
            label = day
        else:
            if start_at is None and end_at is None:
                raise FileError("读取永久记忆需要 date 或 from/to 指定时间范围")
            start = _parse_moment(start_at, self._timezone) if start_at else None
            end = _parse_moment(end_at, self._timezone) if end_at else None
            if start is not None and end is not None and start > end:
                raise FileError("from 不能晚于 to")
            date_from = (start - timedelta(days=1)).date().isoformat() if start else None
            date_to = (end + timedelta(days=1)).date().isoformat() if end else None
            views = self._daily.search(date_from=date_from, date_to=date_to, persona_id=persona)
            chosen = []
            for view in views:
                at = _moment(view.created_at).astimezone(self._timezone)
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
        return [_render_permanent(view, self._timezone) for view in ordered]

    def _enforce_limit(self, content: str, label: str) -> None:
        try:
            limit = self._limit()
        except Exception as exc:
            raise FileError(f"{label}无法确定单次读取上限：{exc}") from exc
        try:
            count = self._counter(limit.tokenizer).count(content)
        except TokenCountError as exc:
            raise FileError(f"{label}的 token 计数失败，无法确认读取上限：{exc}") from exc
        if count.tokens > limit.max_tokens:
            estimate = "估算值约 " if count.estimated else "约 "
            raise FileError(
                f"{label}结果需要{estimate}{count.tokens} token，"
                f"超过单次读取上限 {limit.max_tokens} token；请缩小时间范围或先用 grep 定位"
            )

    def _limit(self) -> ModelLimit:
        limit = self._limit_provider()
        if limit.max_tokens <= 0:
            raise FileError("子代理模型的 max_tokens 必须大于 0")
        return limit

    def _counter(self, tokenizer: str) -> TokenCounter:
        with self._counter_lock:
            counter = self._counters.get(tokenizer)
            if counter is None:
                counter = self._counter_factory(tokenizer)
                self._counters[tokenizer] = counter
            return counter

    def _grep_tree(self, resource: Resource, regex, limit: int) -> str:
        assert resource.path is not None
        root = resource.path
        context = self._context()
        pending = {
            snapshot.abs_path: snapshot
            for snapshot in self._editor.pending(context.main_task_id)
            if snapshot.change.resource == WORLD
        }
        matches: list[str] = []
        truncated = False
        visited: set[Path] = set()
        for current, directories, files in os.walk(root):
            directories[:] = [
                name for name in sorted(directories) if not _is_link(Path(current) / name)
            ]
            for name in sorted(files):
                target = Path(current) / name
                if _is_link(target):
                    continue
                visited.add(target)
                snapshot = pending.get(target)
                if snapshot is not None and snapshot.delete_file:
                    continue
                if snapshot is not None and snapshot.writes > 0:
                    source = split_lines(snapshot.change.staged_text)
                else:
                    try:
                        source = _lines_of(target)
                    except (OSError, UnicodeDecodeError):
                        continue
                relative = _relative(target, root)
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
            relative = _relative(target, root)
            for number, line in enumerate(split_lines(snapshot.change.staged_text), start=1):
                if regex.search(line):
                    matches.append(f"{relative}:{number}: {line}")
                    if len(matches) >= limit:
                        truncated = True
                        break
        return _grep_text(matches, truncated)

    def _staging_target(self, resource: Resource) -> tuple[str, str, str] | None:
        """返回（资源 ID、暂存键、展示路径）；只有 draft 与 world 进入草稿层。"""
        if resource.path is None:
            return None
        if resource.spec.id == DRAFT:
            name = resource.path.name
            return DRAFT, f"{DRAFT}:{name}", name
        if resource.spec.id == WORLD:
            display = _relative(resource.path, resource.root)
            return WORLD, f"{WORLD}:{display}", display
        return None

    def _settle_active(self, action: str, resource: str | None, key: str | None) -> tuple[str, ...]:
        context = self._context()
        return self._editor.before_access(
            context.main_task_id,
            context.subtask_id,
            action,
            resource or "",
            key,
        )

    def _approve_read(
        self,
        *,
        action: str,
        topic: str,
        task: str,
        parameters: str,
        resource: str | None = None,
        path: str | None = None,
    ) -> None:
        """用户来源的读取在真正读取前逐次审批；来源与身份只由后端上下文决定。"""
        if self._read_gate is None:
            return
        context = self._context()
        if context.source != SOURCE_USER:
            return
        try:
            outcome = self._read_gate.request_read(
                context,
                ReadApprovalRequest(
                    action=action,
                    topic=topic,
                    task=task,
                    parameters=parameters,
                    resource=resource,
                    path=path,
                ),
            )
        except Exception as exc:
            raise FileError(f"「{topic}」读取审批失败：{exc}") from exc
        if outcome.approved:
            return
        reason = outcome.reason or "用户拒绝"
        raise FileError(f"「{topic}」未获批准（{reason}）；本次未读取，任务可以继续")

    def _require_staged(self, task: str, key: str, display: str) -> StagedSnapshot:
        snapshot = self._editor.snapshot(task, key)
        if snapshot is None:
            raise FileError(f"尚未读取 {display}；请先 read 再编辑")
        return snapshot

    def _writer(self) -> MemoryWriter:
        return MemoryWriter(self._structured)

    def _context(self) -> TaskContext:
        """取任务身份；工具本身不携带身份参数，一律由后端在派发时注入。"""
        try:
            return current_task()
        except TaskContextError as exc:
            raise FileError(f"文件访问缺少任务身份：{exc}") from exc

    def _persona(self) -> str:
        """按任务人设读取记忆，避免执行中用户切换人设造成身份漂移。"""
        return self._context().persona_id

    def _authorize(self, resource: Resource, action: str) -> TaskContext:
        """先查资源登记的硬限制，再查派发时的权限快照；两关都过才放行。"""
        self._resolver.require(resource, action)
        context = self._context()
        resource_id = resource.spec.id
        if context.permissions.allows(resource_id, action):
            return context
        allowed = "、".join(sorted(context.permissions.actions_for(resource_id)))
        raise FileDenied(f"{resource.spec.label} 不支持 {action} 操作；允许的操作：{allowed}")

    def _writable(self, resource: str, path: str | None, action: str) -> Resource:
        resolved = self._resolver.resolve(resource, path)
        self._authorize(resolved, action)
        if resolved.path is None:
            raise FileError(f"{resolved.spec.label}没有可写入的文件")
        return resolved


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _content_block(content: str) -> list[str]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not normalized.strip():
        raise FileError("内容不能为空")
    return normalized.split("\n")


def _lines_of(path: Path) -> list[str]:
    return split_lines(io.read_text(path))


def _read_text(path: Path) -> str:
    try:
        return io.read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise FileError(f"不是 UTF-8 文本：{path.name}") from exc


def _text_state(text: str) -> tuple[list[str], bool]:
    """全文转行并保留末尾换行状态，供暂存动作在 commit_text 上应用。"""
    if text == "":
        return [], False
    trailing = text.endswith("\n")
    body = text[:-1] if trailing else text
    return body.split("\n"), trailing


def _join_text(lines: Sequence[str], trailing: bool) -> str:
    body = "\n".join(lines)
    return f"{body}\n" if trailing and body else body


def _with_receipts(text: str, receipts: Sequence[str]) -> str:
    if not receipts:
        return text
    return f"{text}\n" + "\n".join(receipts)


def _with_settlement(payload: dict[str, object], receipts: Sequence[str]) -> dict[str, object]:
    if receipts:
        payload["settlement"] = list(receipts)
    return payload


def _read_lines(path: Path) -> tuple[list[str], bool]:
    normalized = io.read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    if normalized == "":
        return [], False
    trailing = normalized.endswith("\n")
    body = normalized[:-1] if trailing else normalized
    return body.split("\n"), trailing


def _write_lines(path: Path, lines: Sequence[str], trailing: bool) -> None:
    body = "\n".join(lines)
    io.write_text_atomic(path, f"{body}\n" if trailing and body else body)


def _require_file(path: Path) -> Path:
    if not path.exists():
        raise FileError(f"文件不存在：{path.name}")
    if path.is_dir():
        raise FileError("目标是目录，请指定文件")
    return path


def _checked_ranges(source: Sequence[str], lines: Sequence[Sequence[int]]):
    try:
        return checked_ranges(source, lines)
    except MemoryReadError as exc:
        raise FileError(str(exc)) from exc


def _replace_ranges(
    source: Sequence[str],
    ranges: Sequence[tuple[int, int]],
    regex,
    replacement: str,
) -> tuple[list[str], int, int, int]:
    updated = list(source)
    hits = 0
    first = len(source) + 1
    last = 0
    for start, end in reversed(ranges):
        target = "\n".join(updated[start - 1 : end])
        replaced, count = regex.subn(lambda _match: replacement, target)
        if not count:
            continue
        hits += count
        block = replaced.split("\n")
        updated[start - 1 : end] = block
        first = min(first, start)
        last = max(last, start + len(block) - 1)
    return updated, hits, first, last


def _grep_lines(
    source: Sequence[str],
    regex,
    lines: Sequence[Sequence[int]] | None,
    limit: int,
) -> str:
    ranges = _checked_ranges(source, lines) if lines else None
    matches: list[str] = []
    truncated = False
    for number, line in enumerate(source, start=1):
        if ranges is not None and not any(start <= number <= end for start, end in ranges):
            continue
        if regex.search(line):
            matches.append(f"{number}: {line}")
            if len(matches) >= limit:
                truncated = True
                break
    if not matches:
        return "没有匹配的内容"
    return _grep_text(matches, truncated)


def _grep_text(matches: Sequence[str], truncated: bool) -> str:
    if truncated:
        return "\n".join([*matches, f"……（仅显示前 {len(matches)} 条匹配，请缩小范围）"])
    return "\n".join(matches)


def _render_permanent(view, timezone: tzinfo) -> str:
    at = _moment(view.created_at).astimezone(timezone)
    text = view.content.replace("\r\n", "\n").replace("\n", " ")
    if view.role == "event":
        text = f"事件：{text}"
    return f"[{at:%Y-%m-%d %H:%M:%S}][{view.role}]{text}"


def _parse_moment(value: str, timezone: tzinfo) -> datetime:
    try:
        if len(value) == 10:
            parsed = datetime.combine(date.fromisoformat(value), time.min)
        else:
            parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FileError(f"时间格式不合法：{value}；应为 YYYY-MM-DD 或 ISO 8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _day_text(day: str) -> str:
    try:
        return date.fromisoformat(day).isoformat()
    except ValueError as exc:
        raise FileError(f"日期格式不合法：{day}") from exc


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _approval_target(resource: Resource) -> str:
    """审批卡展示的目标：带资源前缀的路径，目录与虚拟资源用可读名称。"""
    spec = resource.spec
    if resource.path is None:
        return f"{spec.id}（{spec.label}）"
    if spec.id == WORLD:
        if resource.path == resource.root:
            return "world（目录）"
        return f"world/{_relative(resource.path, resource.root)}"
    if resource.path.is_dir():
        return f"{spec.id}（{spec.label}）"
    return f"{spec.id}/{resource.path.name}"


def _approval_path(resource: Resource) -> str | None:
    if resource.path is None or resource.path.is_dir():
        return None
    if resource.spec.id == WORLD:
        return _relative(resource.path, resource.root)
    return resource.path.name


def _read_parameters(
    line_ranges: Sequence[Sequence[int]] | None,
    day: str | None,
    start_at: str | None,
    end_at: str | None,
) -> str:
    parts: list[str] = []
    if line_ranges:
        parts.append(f"line_ranges={[list(item) for item in line_ranges]}")
    if day:
        parts.append(f"date={day}")
    if start_at:
        parts.append(f"from={start_at}")
    if end_at:
        parts.append(f"to={end_at}")
    return "；".join(parts) or "无额外参数"


def _grep_parameters(
    pattern: str,
    line_ranges: Sequence[Sequence[int]] | None,
    day: str | None,
    start_at: str | None,
    end_at: str | None,
    limit: int,
) -> str:
    parts = [f"pattern={pattern!r}"]
    if line_ranges:
        parts.append(f"line_ranges={[list(item) for item in line_ranges]}")
    if day:
        parts.append(f"date={day}")
    if start_at:
        parts.append(f"from={start_at}")
    if end_at:
        parts.append(f"to={end_at}")
    parts.append(f"limit={limit}")
    return "；".join(parts)


def _display(resource: Resource) -> str:
    if resource.spec.id == WORLD:
        return "resources"
    assert resource.path is not None
    return resource.path.name


def _file_display(resource: Resource) -> str:
    if resource.spec.id == WORLD:
        assert resource.path is not None
        return _relative(resource.path, resource.root)
    assert resource.path is not None
    return resource.path.name


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _span_text(lines: Sequence[Sequence[int]]) -> str:
    ranges = parse_ranges(lines)
    return "、".join(f"{start}" if start == end else f"{start}-{end}" for start, end in ranges)
