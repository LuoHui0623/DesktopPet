"""统一文件访问服务：八个动作统一按资源权限执行。"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo

from constants.files import (
    ACTION_APPEND,
    ACTION_DELETE,
    ACTION_GREP,
    ACTION_INDEX,
    ACTION_INSERT,
    ACTION_READ,
    ACTION_REPLACE_LINES,
    ACTION_REPLACE_REGEX,
    PERMANENT,
    WORLD,
)
from infra.llm.tokens import TokenCounter
from module.agent.approval import ReadApprovalRequest, ReadApprover
from module.agent.context import SOURCE_USER, TaskContext, TaskContextError, current_task
from module.agent.skills import SkillRegistry
from module.files import token as snapshot_token
from module.files.format import relative_path
from module.files.handlers import (
    GREP_LIMIT,
    ROUTES,
    HandlerDeps,
    ModelLimit,
    Query,
    ReadLimiter,
    overview,
    staging_target,
)
from module.files.observability import traced
from module.files.resolver import FileDenied, FileError, Resource, ResourceResolver
from module.files.staging import Editor, StagedSnapshot
from module.files.text import checked, join_text, read_text, text_state
from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService
from module.memory.memory_service import (
    compile_pattern,
    expect_matches,
    parse_ranges,
    replace_ranges,
)


@dataclass(frozen=True)
class _WriteSession:
    """一次写动作的公共上下文；避免五个写方法各自重复解包同一批字段。"""

    task: str
    author: str
    resource: str
    key: str
    display: str
    receipts: list[str]


def _system_now(zone: tzinfo) -> datetime:
    return datetime.now(zone)


class FileService:
    def __init__(
        self,
        resolver: ResourceResolver,
        entries: MemoryEntryService,
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
        self._entries = entries
        self._daily = daily
        self._skills = skills
        self._editor = editor
        self._read_gate = read_gate
        self._timezone = timezone
        self._clock = clock or _system_now
        self._limiter = ReadLimiter(limit_provider, counter_factory=counter_factory)
        self._deps = HandlerDeps(
            entries=entries,
            daily=daily,
            skills=skills,
            editor=editor,
            limiter=self._limiter,
            timezone=timezone,
            context=self._context,
        )

    @property
    def editor(self) -> Editor:
        return self._editor

    @traced(ACTION_INDEX)
    def index(self, resource: str | None = None, path: str | None = None) -> str:
        """列出资源索引；流程固定为审批 → 结算 → 交处理器。"""
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
            payload = ROUTES["skill"].index(self._deps, None, Query())
            return _json(_with_settlement(payload, receipts))
        if resource is None and path is None:
            self._approve_read(
                action=ACTION_INDEX,
                topic="读取资源总览",
                task="index：查看资源、权限与下一步提示",
                parameters="无额外参数",
            )
            receipts = self._settle_active(ACTION_INDEX, resource, None)
            return _json(_with_settlement(overview(self._deps), receipts))
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
        payload = ROUTES[resolved.spec.id].index(self._deps, resolved, Query())
        return _json(_with_settlement(payload, receipts))

    @traced(ACTION_READ)
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
            content = ROUTES["skill"].read(self._deps, None, Query(name=name))
            return _with_receipts(content, receipts)
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
        query = Query(
            name=name,
            line_ranges=line_ranges,
            day=day,
            start_at=start_at,
            end_at=end_at,
        )
        content = ROUTES[resolved.spec.id].read(self._deps, resolved, query)
        return _with_receipts(content, receipts)

    @traced(ACTION_GREP)
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
        query = Query(
            line_ranges=line_ranges,
            day=day,
            start_at=start_at,
            end_at=end_at,
            pattern=pattern,
            limit=limit,
        )
        content = ROUTES[resolved.spec.id].grep(self._deps, resolved, query, regex)
        return _with_receipts(content, receipts)

    @traced(ACTION_APPEND)
    def append(
        self,
        resource: str,
        content: str,
        path: str | None = None,
        token: str | None = None,
    ) -> str:
        resolved = self._writable(resource, path, ACTION_APPEND)
        block = _content_block(content)
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定文件")
        session = self._open_write(resolved, ACTION_APPEND)
        with self._editor.lock_for(resolved.path):
            snapshot = self._editor.snapshot(session.task, session.key)
            if snapshot is None:
                if resolved.spec.id == WORLD and not resolved.path.exists():
                    self._editor.establish(
                        session.task,
                        resource=session.resource,
                        key=session.key,
                        display=session.display,
                        abs_path=resolved.path,
                        text="",
                        created=True,
                    )
                    snapshot = self._editor.snapshot(session.task, session.key)
                else:
                    raise FileError(f"尚未读取 {session.display}；请先 read 再编辑")
            assert snapshot is not None
            if snapshot.delete_file:
                raise FileError(f"{session.display} 已暂存为删除，不能再编辑")
            self._guard(snapshot, resolved, session, token)
            source, trailing = text_state(snapshot.change.staged_text)
            if snapshot.created and not source:
                trailing = True
            lines = [*source, *block]
            start = len(lines) - len(block) + 1
            self._editor.record(
                session.task,
                session.author,
                session.key,
                ACTION_APPEND,
                join_text(lines, trailing),
                False,
            )
        session.receipts.extend(self._editor.after_write(session.task, session.author, session.key))
        message = (
            f"已暂存，尚未写入文件；已追加至 {session.display} 第 "
            f"{start}-{start + len(block) - 1} 行"
        )
        return _with_receipts(message, session.receipts)

    @traced(ACTION_INSERT)
    def insert(
        self,
        resource: str,
        line: int,
        content: str,
        path: str | None = None,
        token: str | None = None,
    ) -> str:
        resolved = self._writable(resource, path, ACTION_INSERT)
        block = _content_block(content)
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定文件")
        session = self._open_write(resolved, ACTION_INSERT)
        with self._editor.lock_for(resolved.path):
            snapshot = self._require_staged(session.task, session.key, session.display)
            if snapshot.delete_file:
                raise FileError(f"{session.display} 已暂存为删除，不能再编辑")
            self._guard(snapshot, resolved, session, token)
            source, trailing = text_state(snapshot.change.staged_text)
            if not 1 <= line <= len(source) + 1:
                raise FileError(f"插入位置 {line} 超出范围：应为 1–{len(source) + 1}")
            lines = [*source[: line - 1], *block, *source[line - 1 :]]
            self._editor.record(
                session.task,
                session.author,
                session.key,
                ACTION_INSERT,
                join_text(lines, trailing),
                False,
            )
        session.receipts.extend(self._editor.after_write(session.task, session.author, session.key))
        message = (
            f"已暂存，尚未写入文件；已插入 {session.display} 第 {line}-{line + len(block) - 1} 行"
        )
        return _with_receipts(message, session.receipts)

    @traced(ACTION_REPLACE_LINES)
    def edit(
        self,
        resource: str,
        line_ranges: Sequence[Sequence[int]],
        content: str,
        path: str | None = None,
        expect: str | Sequence[str] | None = None,
        token: str | None = None,
    ) -> str:
        if not line_ranges:
            raise FileError("整段替换需要提供行号范围 line_ranges；删除整段内容请用 delete")
        resolved = self._writable(resource, path, ACTION_REPLACE_LINES)
        block = _content_block(content)
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定文件")
        session = self._open_write(resolved, ACTION_REPLACE_LINES)
        with self._editor.lock_for(resolved.path):
            snapshot = self._require_staged(session.task, session.key, session.display)
            if snapshot.delete_file:
                raise FileError(f"{session.display} 已暂存为删除，不能再编辑")
            self._guard(snapshot, resolved, session, token)
            source, trailing = text_state(snapshot.change.staged_text)
            ranges = checked(source, line_ranges)
            expect_matches(source, ranges, expect)
            updated = replace_ranges(source, ranges, block)
            self._editor.record(
                session.task,
                session.author,
                session.key,
                ACTION_REPLACE_LINES,
                join_text(updated, trailing),
                False,
            )
        session.receipts.extend(self._editor.after_write(session.task, session.author, session.key))
        shift = sum(len(block) - (end - start + 1) for start, end in ranges[:-1])
        message = (
            f"已暂存，尚未写入文件；已编辑 {session.display} 第 "
            f"{ranges[0][0]}-{ranges[-1][0] + shift + len(block) - 1} 行"
        )
        return _with_receipts(message, session.receipts)

    @traced(ACTION_REPLACE_REGEX)
    def replace(
        self,
        resource: str,
        line_ranges: Sequence[Sequence[int]],
        pattern: str,
        content: str,
        path: str | None = None,
        token: str | None = None,
    ) -> str:
        if not line_ranges:
            raise FileError("正则替换需要提供行号范围 line_ranges；整段替换请用 replace_lines")
        resolved = self._writable(resource, path, ACTION_REPLACE_REGEX)
        regex = compile_pattern(pattern)
        replacement = "\n".join(_content_block(content))
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定文件")
        session = self._open_write(resolved, ACTION_REPLACE_REGEX)
        with self._editor.lock_for(resolved.path):
            snapshot = self._require_staged(session.task, session.key, session.display)
            if snapshot.delete_file:
                raise FileError(f"{session.display} 已暂存为删除，不能再编辑")
            self._guard(snapshot, resolved, session, token)
            source, trailing = text_state(snapshot.change.staged_text)
            ranges = checked(source, line_ranges)
            updated, hits, first, last = _replace_ranges(source, ranges, regex, replacement)
            if not hits:
                raise FileError(f"正则 {pattern!r} 没有匹配到内容，请先用 read 确认原文")
            self._editor.record(
                session.task,
                session.author,
                session.key,
                ACTION_REPLACE_REGEX,
                join_text(updated, trailing),
                False,
            )
        session.receipts.extend(self._editor.after_write(session.task, session.author, session.key))
        message = (
            f"已暂存，尚未写入文件；已替换 {session.display} 共 {hits} 处，位于第 {first}-{last} 行"
        )
        return _with_receipts(message, session.receipts)

    @traced(ACTION_DELETE)
    def delete(
        self,
        resource: str,
        path: str | None = None,
        line_ranges: Sequence[Sequence[int]] | None = None,
        expect: str | Sequence[str] | None = None,
        token: str | None = None,
    ) -> str:
        resolved = self._writable(resource, path, ACTION_DELETE)
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
        session = self._open_write(resolved, ACTION_DELETE)
        if line_ranges is None:
            with self._editor.lock_for(resolved.path):
                snapshot = self._require_staged(session.task, session.key, session.display)
                if snapshot.delete_file:
                    raise FileError(f"{session.display} 已暂存为删除")
                self._guard(snapshot, resolved, session, token)
                self._editor.record(
                    session.task,
                    session.author,
                    session.key,
                    "delete_file",
                    "",
                    True,
                )
            session.receipts.extend(
                self._editor.after_write(session.task, session.author, session.key)
            )
            return _with_receipts(
                f"已暂存，尚未删除文件；已标记删除 {session.display}", session.receipts
            )
        with self._editor.lock_for(resolved.path):
            snapshot = self._require_staged(session.task, session.key, session.display)
            if snapshot.delete_file:
                raise FileError(f"{session.display} 已暂存为删除，不能再编辑")
            self._guard(snapshot, resolved, session, token)
            source, trailing = text_state(snapshot.change.staged_text)
            ranges = checked(source, line_ranges)
            expect_matches(source, ranges, expect)
            for start, end in reversed(ranges):
                del source[start - 1 : end]
            self._editor.record(
                session.task,
                session.author,
                session.key,
                ACTION_DELETE,
                join_text(source, trailing),
                False,
            )
        session.receipts.extend(self._editor.after_write(session.task, session.author, session.key))
        return _with_receipts(
            f"已暂存，尚未写入文件；已删除 {session.display} 第 {_span_text(line_ranges)} 行",
            session.receipts,
        )

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

    def _open_write(self, resolved: Resource, action: str) -> _WriteSession:
        """写动作的公共开场：取身份、定位暂存键、按文件开票。"""
        assert resolved.path is not None
        context = self._context()
        target = staging_target(resolved)
        assert target is not None
        resource_id, key, display = target
        receipts = list(
            self._editor.before_access(
                context.main_task_id, context.subtask_id, action, resource_id, key
            )
        )
        return _WriteSession(
            task=context.main_task_id,
            author=context.subtask_id,
            resource=resource_id,
            key=key,
            display=display,
            receipts=receipts,
        )

    def _guard(
        self,
        snapshot: StagedSnapshot,
        resolved: Resource,
        session: _WriteSession,
        token: str | None,
    ) -> None:
        """编辑期两道校验：令牌匹配、磁盘与读取留底一致。

        只在该文件尚未写入过时比对磁盘；写入之后 base 冻结，令牌即稳定凭据。
        """
        if token is not None:
            snapshot_token.require_match(snapshot.version, token, session.display)
        if snapshot.writes or snapshot.created:
            return
        assert resolved.path is not None
        try:
            disk = read_text(resolved.path)
        except FileNotFoundError as exc:
            raise snapshot_token.StaleSnapshot(
                f"{session.display} 已不存在；请重新 index 查看目录后再操作"
            ) from exc
        if disk != snapshot.change.base_text:
            raise snapshot_token.StaleSnapshot(
                f"{session.display} 在读取之后已被修改；请重新 read 后再编辑"
            )

    def _context(self) -> TaskContext:
        """取任务身份；工具本身不携带身份参数，一律由后端在派发时注入。"""
        try:
            return current_task()
        except TaskContextError as exc:
            raise FileError(f"文件访问缺少任务身份：{exc}") from exc

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


def _with_receipts(text: str, receipts: Sequence[str]) -> str:
    if not receipts:
        return text
    return f"{text}\n" + "\n".join(receipts)


def _with_settlement(payload: dict[str, object], receipts: Sequence[str]) -> dict[str, object]:
    if receipts:
        payload["settlement"] = list(receipts)
    return payload


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


def _approval_target(resource: Resource) -> str:
    """审批卡展示的目标：带资源前缀的路径，目录与虚拟资源用可读名称。"""
    spec = resource.spec
    if resource.path is None:
        return f"{spec.id}（{spec.label}）"
    if spec.id == WORLD:
        if resource.path == resource.root:
            return "world（目录）"
        return f"world/{relative_path(resource.path, resource.root)}"
    if resource.path.is_dir():
        return f"{spec.id}（{spec.label}）"
    return f"{spec.id}/{resource.path.name}"


def _approval_path(resource: Resource) -> str | None:
    if resource.path is None or resource.path.is_dir():
        return None
    if resource.spec.id == WORLD:
        return relative_path(resource.path, resource.root)
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


def _span_text(lines: Sequence[Sequence[int]]) -> str:
    ranges = parse_ranges(lines)
    return "、".join(f"{start}" if start == end else f"{start}-{end}" for start, end in ranges)
