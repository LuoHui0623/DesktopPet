"""统一文件服务：索引、读取、检索与写动作。

写动作只做三件事——归一为 range 条目、入队、返回回执。**不落盘、不审批**：
落盘发生在 review 之后的提交，人工授权只有 review 一处。
"""

import json
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path

from constants.files import (
    ACTION_APPEND,
    ACTION_CREATE_DIR,
    ACTION_CREATE_FILE,
    ACTION_DELETE_DIR,
    ACTION_DELETE_FILE,
    ACTION_DELETE_LINES,
    ACTION_GREP,
    ACTION_INDEX,
    ACTION_INSERT,
    ACTION_READ,
    ACTION_REPLACE_LINES,
    ACTION_REPLACE_REGEX,
    DOMAIN_FILES,
    DOMAIN_MEMORY,
    DOMAIN_SKILLS,
    DOMAIN_TOOLS,
    LINES_APPEND,
    LINES_DELETE,
    LINES_INSERT,
    LINES_REPLACE,
    PERMANENT,
    RESOURCES,
    STRUCTURED,
    WORLD,
    normalize_domain,
)
from infra.llm.tokens import TokenCounter
from module.agent.context import TaskContext, TaskContextError, current_task
from module.agent.skills import SkillRegistry
from module.files import catalog
from module.files import token as snapshot_token
from module.files.format import relative_path
from module.files.guard import (
    STATE_FREE,
    FileGuard,
    check_version,
    describe_spans,
    normalize_text,
    revalidate,
)
from module.files.handlers import (
    GREP_LIMIT,
    ROUTES,
    HandlerDeps,
    ModelLimit,
    Query,
    ReadLimiter,
)
from module.files.observability import traced
from module.files.ranges import (
    ChangeQueue,
    LineRange,
    append_span,
    apply_actions,
    closed_ranges,
    whole_file_span,
)
from module.files.resolver import FileDenied, FileError, Resource, ResourceResolver
from module.files.session import SessionStore, TaskSession, TrackedFile
from module.files.text import numbered, read_text, text_state
from module.files.views import (
    DirEntries,
    FileIndex,
    FileSupport,
    MemoryIndex,
    SkillIndex,
    ToolIndex,
    payload,
)
from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService
from module.memory.memory_service import compile_pattern, expect_matches, parse_ranges


@dataclass(frozen=True)
class _Editing:
    """一次写动作的作用域：会话、读取版本与解析后的资源。"""

    session: TaskSession
    tracked: TrackedFile
    resource: Resource
    author: str

    @property
    def queue(self) -> ChangeQueue:
        return self.session.queue


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
        sessions: SessionStore,
        guard: FileGuard,
        timezone: tzinfo,
        limit_provider: Callable[[], ModelLimit],
        clock: Callable[[tzinfo], datetime] | None = None,
        counter_factory: Callable[[str], TokenCounter] | None = None,
    ) -> None:
        self._resolver = resolver
        self._entries = entries
        self._daily = daily
        self._skills = skills
        self._sessions = sessions
        self._guard = guard
        self._timezone = timezone
        self._clock = clock or _system_now
        self._limiter = ReadLimiter(limit_provider, counter_factory=counter_factory)
        self._deps = HandlerDeps(
            entries=entries,
            daily=daily,
            skills=skills,
            limiter=self._limiter,
            timezone=timezone,
            context=self._context,
        )

    # —— 索引 ——

    @traced(ACTION_INDEX)
    def index(self, target: str) -> str:
        """单一入参：命中领域保留字给领域视图，命中目录给目录视图。"""
        domain = normalize_domain(target)
        if domain is not None:
            return _json(payload(self._domain(domain).explore(target)))
        context = self._context()
        world = _resolver_world(self._resolver)
        entries = catalog.entries_for_target(
            target, world, context.permissions, self._guard, context.main_task_id
        )
        return _json(payload(entries))

    def _domain(self, domain: str) -> FileIndex | SkillIndex | ToolIndex | MemoryIndex:
        if domain == DOMAIN_FILES:
            return FileIndex(
                support=self._file_support,
                entries=lambda target: self._dir_entries(target),
            )
        if domain == DOMAIN_SKILLS:
            return SkillIndex(self._skill_items)
        if domain == DOMAIN_TOOLS:
            return ToolIndex(lambda: catalog.tool_listing(self._context().permissions))
        if domain == DOMAIN_MEMORY:
            specs = [RESOURCES[STRUCTURED], RESOURCES[PERMANENT]]
            return MemoryIndex(lambda: catalog.memory_listing(specs))
        raise FileError(f"未知领域：{domain}")

    def _file_support(self) -> FileSupport:
        return catalog.file_support(self._context().permissions)

    def _dir_entries(self, target: str) -> DirEntries:
        context = self._context()
        return catalog.entries_for_target(
            target,
            _resolver_world(self._resolver),
            context.permissions,
            self._guard,
            context.main_task_id,
        )

    def _skill_items(self) -> list[dict[str, object]]:
        return catalog.skill_listing(
            [{"name": item.name, "description": item.description} for item in self._skills.index()]
        )

    # —— 读取 ——

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
                raise FileError("读取技能说明书需要 name；先用 index 查看 SKILLS 清单")
            return ROUTES["skill"].read(self._deps, Query(name=name))
        resolved = self._resolver.resolve(resource, path)
        self._authorize(resolved, ACTION_READ)
        if resolved.spec.id in (STRUCTURED, PERMANENT):
            query = Query(line_ranges=line_ranges, day=day, start_at=start_at, end_at=end_at)
            return ROUTES[resolved.spec.id].read(self._deps, query)
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定具体文件；用 index 查看目录内容")
        notice = self._read_text_of(resolved, line_ranges)
        return notice

    def _read_text_of(self, resolved: Resource, line_ranges: Sequence[Sequence[int]] | None) -> str:
        assert resolved.path is not None
        context = self._context()
        session = self._sessions.for_task(context.main_task_id)
        try:
            text = read_text(resolved.path)
            exists = True
        except FileNotFoundError as exc:
            if resolved.spec.id != WORLD:
                raise FileError(f"文件不存在：{resolved.path.name}") from exc
            text, exists = "", False
        notes: list[str] = []
        tracked = session.tracked(str(resolved.path.resolve()))
        if tracked is not None and tracked.h0_text != text:
            result = revalidate(session.queue, tracked.abs_path, tracked.h0_text, text)
            notes.append(
                f"提示：{self._display(resolved)} 已被外部修改（{describe_spans(result.spans)}）；"
                f"已作废 {len(result.invalidated)} 条相交条目，保留 {len(result.kept)} 条"
            )
        tracked = self._track(session, resolved, text, exists)
        body = numbered(text_state(text)[0], line_ranges)
        content = (
            f"{body}\n\n{snapshot_token.token_line(tracked.version)}"
            if body
            else (snapshot_token.token_line(tracked.version))
        )
        in_flight = session.queue.entries_for(tracked.abs_path)
        if in_flight:
            notes.append(
                f"本文件有 {len(in_flight)} 条在途改动，尚未写入磁盘："
                + "；".join(entry.summary() for entry in in_flight)
            )
        status = self._guard.status(resolved.path, context.main_task_id)
        if status.state != STATE_FREE:
            notes.append(f"占用：{status.notice}")
        if notes:
            content = f"{content}\n" + "\n".join(notes)
        return content

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
        if resolved.spec.id in (STRUCTURED, PERMANENT):
            query = Query(
                line_ranges=line_ranges,
                day=day,
                start_at=start_at,
                end_at=end_at,
                pattern=pattern,
                limit=limit,
            )
            return ROUTES[resolved.spec.id].grep(self._deps, query, regex)
        assert resolved.path is not None
        if resolved.path.is_dir():
            return self._grep_dir(resolved, regex, limit)
        session = self._sessions.for_task(self._context().main_task_id)
        source = self._visible_lines(session, resolved)
        return _grep_lines(source, regex, line_ranges, limit)

    def _visible_lines(self, session: TaskSession, resolved: Resource) -> list[str]:
        """编辑者看到自己的已批准条目；其他任务看到磁盘原文。"""
        assert resolved.path is not None
        abs_path = str(resolved.path.resolve())
        tracked = session.tracked(abs_path)
        if tracked is not None:
            actions = session.queue.actions_for(abs_path)
            if actions:
                return apply_actions(tracked.h0_lines, actions)
        try:
            return text_state(read_text(resolved.path))[0]
        except FileNotFoundError as exc:
            raise FileError(f"文件不存在：{resolved.path.name}") from exc

    def _grep_dir(self, resolved: Resource, regex, limit: int) -> str:
        assert resolved.path is not None
        root = resolved.path
        session = self._sessions.for_task(self._context().main_task_id)
        matches: list[str] = []
        for current, directories, files in os.walk(root):
            directories[:] = sorted(
                name for name in directories if not (Path(current) / name).is_symlink()
            )
            for name in sorted(files):
                target = Path(current) / name
                if target.is_symlink():
                    continue
                if session.tracked(str(target.resolve())) is None:
                    try:
                        source = text_state(read_text(target))[0]
                    except (OSError, UnicodeDecodeError):
                        continue
                else:
                    source = self._visible_lines(session, Resource(resolved.spec, target, root))
                relative = relative_path(target, root)
                for number, line in enumerate(source, start=1):
                    if regex.search(line):
                        matches.append(f"{relative}:{number}: {line}")
                        if len(matches) >= limit:
                            return _grep_text(matches, True)
        return _grep_text(matches, False)

    # —— 写动作：只入队 ——

    @traced(ACTION_APPEND)
    def append(
        self,
        resource: str,
        content: str,
        path: str | None = None,
        token: str | None = None,
    ) -> str:
        block = _content_block(content)
        with self._editing(resource, path, ACTION_APPEND, token) as editing:
            if not editing.tracked.exists:
                raise FileError(
                    f"{editing.tracked.path} 不存在；append 不新建文件，请改用 create_file"
                )
            span = append_span(editing.tracked.line_count)
            entry = editing.queue.propose(
                resource=editing.tracked.resource,
                path=editing.tracked.path,
                abs_path=editing.tracked.abs_path,
                author=editing.author,
                op=LINES_APPEND,
                source_action=ACTION_APPEND,
                range_lines=span,
                content=block,
            )
        return _proposed(editing.tracked, entry.entry_id, len(block), "追加")

    @traced(ACTION_INSERT)
    def insert(
        self,
        resource: str,
        line: int,
        content: str,
        path: str | None = None,
        token: str | None = None,
    ) -> str:
        block = _content_block(content)
        with self._editing(resource, path, ACTION_INSERT, token) as editing:
            limit = editing.tracked.line_count + 1
            if not 1 <= line <= limit:
                raise FileError(f"插入位置 {line} 超出范围：应为 1–{limit}")
            entry = editing.queue.propose(
                resource=editing.tracked.resource,
                path=editing.tracked.path,
                abs_path=editing.tracked.abs_path,
                author=editing.author,
                op=LINES_INSERT,
                source_action=ACTION_INSERT,
                range_lines=(line, line),
                content=block,
            )
        return _proposed(editing.tracked, entry.entry_id, len(block), "插入")

    @traced(ACTION_REPLACE_LINES)
    def replace_lines(
        self,
        resource: str,
        line_ranges: Sequence[Sequence[int]],
        content: str,
        path: str | None = None,
        expect: str | Sequence[str] | None = None,
        token: str | None = None,
    ) -> str:
        block = _content_block(content)
        with self._editing(resource, path, ACTION_REPLACE_LINES, token) as editing:
            spans = self._checked_spans(editing.tracked, line_ranges)
            if expect is not None:
                expect_matches(editing.tracked.h0_lines, _closed(spans), expect)
            for span in spans:
                editing.queue.propose(
                    resource=editing.tracked.resource,
                    path=editing.tracked.path,
                    abs_path=editing.tracked.abs_path,
                    author=editing.author,
                    op=LINES_REPLACE,
                    source_action=ACTION_REPLACE_LINES,
                    range_lines=span,
                    content=block,
                )
        return _proposed(editing.tracked, f"{len(spans)} 条", len(block), "整段替换")

    @traced(ACTION_REPLACE_REGEX)
    def replace_regex(
        self,
        resource: str,
        line_ranges: Sequence[Sequence[int]],
        pattern: str,
        content: str,
        path: str | None = None,
        token: str | None = None,
    ) -> str:
        regex = compile_pattern(pattern)
        replacement = "\n".join(_content_block(content))
        with self._editing(resource, path, ACTION_REPLACE_REGEX, token) as editing:
            spans = self._checked_spans(editing.tracked, line_ranges)
            hits = 0
            source = editing.tracked.h0_lines
            for span in spans:
                for number in range(span[0], span[1]):
                    updated, count = regex.subn(lambda _match: replacement, source[number - 1])
                    if not count:
                        continue
                    hits += 1
                    editing.queue.propose(
                        resource=editing.tracked.resource,
                        path=editing.tracked.path,
                        abs_path=editing.tracked.abs_path,
                        author=editing.author,
                        op=LINES_REPLACE,
                        source_action=ACTION_REPLACE_REGEX,
                        range_lines=(number, number + 1),
                        content=tuple(updated.split("\n")),
                    )
            if not hits:
                raise FileError(f"正则 {pattern!r} 在指定范围内没有匹配，请先用 read 确认原文")
        return _proposed(editing.tracked, f"{hits} 条", 1, "正则替换")

    @traced(ACTION_DELETE_LINES)
    def delete_lines(
        self,
        resource: str,
        line_ranges: Sequence[Sequence[int]],
        path: str | None = None,
        expect: str | Sequence[str] | None = None,
        token: str | None = None,
    ) -> str:
        with self._editing(resource, path, ACTION_DELETE_LINES, token) as editing:
            spans = self._checked_spans(editing.tracked, line_ranges)
            if expect is not None:
                expect_matches(editing.tracked.h0_lines, _closed(spans), expect)
            for span in spans:
                editing.queue.propose(
                    resource=editing.tracked.resource,
                    path=editing.tracked.path,
                    abs_path=editing.tracked.abs_path,
                    author=editing.author,
                    op=LINES_DELETE,
                    source_action=ACTION_DELETE_LINES,
                    range_lines=span,
                    content=(),
                )
        removed = sum(span[1] - span[0] for span in spans)
        return _proposed(editing.tracked, f"{len(spans)} 条", removed, "删除内容")

    @traced(ACTION_CREATE_FILE)
    def create_file(
        self,
        resource: str,
        path: str,
        content: str | None = None,
        token: str | None = None,
    ) -> str:
        resolved = self._resolver.resolve(resource, path)
        assert resolved.path is not None
        context = self._context()
        session = self._sessions.for_task(context.main_task_id)
        owned = self._guard.acquire(resolved.path, context.main_task_id)
        completed = False
        try:
            self._authorize(resolved, ACTION_CREATE_FILE)
            if resolved.path.exists():
                raise FileError(f"{self._display(resolved)} 已存在；改内容请用 read 后再编辑")
            block = _content_block(content) if content is not None else []
            tracked = session.track(
                resource=resolved.spec.id,
                path=self._display(resolved),
                abs_path=str(resolved.path.resolve()),
                text="",
                exists=False,
                creates_file=True,
            )
            entry = session.queue.propose(
                resource=tracked.resource,
                path=tracked.path,
                abs_path=tracked.abs_path,
                author=context.subtask_id,
                op=LINES_APPEND,
                source_action=ACTION_CREATE_FILE,
                range_lines=(1, 1),
                content=tuple(block),
                creates_file=True,
            )
            completed = True
        finally:
            if not completed and owned:
                self._guard.release(resolved.path, context.main_task_id)
        return _proposed(tracked, entry.entry_id, len(block), "新建文件")

    @traced(ACTION_DELETE_FILE)
    def delete_file(
        self,
        resource: str,
        path: str,
        token: str | None = None,
    ) -> str:
        with self._editing(resource, path, ACTION_DELETE_FILE, token) as editing:
            if not editing.tracked.exists:
                raise FileError(f"{editing.tracked.path} 不存在，无需删除")
            editing.queue.propose(
                resource=editing.tracked.resource,
                path=editing.tracked.path,
                abs_path=editing.tracked.abs_path,
                author=editing.author,
                op=LINES_DELETE,
                source_action=ACTION_DELETE_FILE,
                range_lines=whole_file_span(editing.tracked.line_count),
                content=(),
                removes_file=True,
            )
            editing.session.mark(editing.tracked.abs_path, removes_file=True)
        return _proposed(editing.tracked, "1 条", editing.tracked.line_count, "删除文件")

    @traced(ACTION_CREATE_DIR)
    def create_dir(self, resource: str, path: str) -> str:
        resolved = self._writable(resource, path, ACTION_CREATE_DIR)
        assert resolved.path is not None
        if resolved.path.exists():
            raise FileError(f"目录已存在：{self._display(resolved)}")
        entry = self._propose_structure(resolved, ACTION_CREATE_DIR)
        return _proposed_structure(resolved, entry.entry_id, "新建目录")

    @traced(ACTION_DELETE_DIR)
    def delete_dir(self, resource: str, path: str) -> str:
        resolved = self._writable(resource, path, ACTION_DELETE_DIR)
        assert resolved.path is not None
        if not resolved.path.exists():
            raise FileError(f"目录不存在：{self._display(resolved)}")
        if not resolved.path.is_dir():
            raise FileError(f"{self._display(resolved)} 不是目录；删除文件请用 delete_file")
        remaining = _child_count(resolved.path)
        if remaining:
            raise FileError(
                f"目录非空：{self._display(resolved)} 下还有 {remaining} 个条目；"
                "请先逐项删除后再删除目录，不提供递归删除"
            )
        entry = self._propose_structure(resolved, ACTION_DELETE_DIR)
        return _proposed_structure(resolved, entry.entry_id, "删除空目录")

    # —— 内部 ——

    def _propose_structure(self, resolved: Resource, action: str):
        assert resolved.path is not None
        context = self._context()
        session = self._sessions.for_task(context.main_task_id)
        return session.queue.propose(
            resource=resolved.spec.id,
            path=self._display(resolved),
            abs_path=str(resolved.path.resolve()),
            author=context.subtask_id,
            op=action,
            source_action=action,
        )

    @contextmanager
    def _editing(
        self, resource: str, path: str | None, action: str, token: str | None
    ) -> Iterator[_Editing]:
        """先占后审：检查并取得占用 → 权限校验 → 版本校验 → 计算 range。

        占用在成功后继续持有，直到提交、取消或任务结束；任何一步失败都视为本次
        动作取消，立即释放，不留下空占用。
        """
        resolved = self._resolver.resolve(resource, path)
        context = self._context()
        if resolved.path is None:
            # 虚拟资源没有可写文件：先走权限校验，给出「不支持该动作」而不是断言。
            self._authorize(resolved, action)
            raise FileError(f"{resolved.spec.label}没有可写入的文件")
        session = self._sessions.for_task(context.main_task_id)
        abs_path = str(resolved.path.resolve())
        owned = self._guard.acquire(resolved.path, context.main_task_id)
        completed = False
        try:
            self._authorize(resolved, action)
            tracked = session.tracked(abs_path)
            if tracked is None:
                raise FileError(f"尚未读取 {self._display(resolved)}；请先 read 再编辑")
            self._verify(session, resolved, tracked)
            tracked = session.tracked(abs_path)
            assert tracked is not None
            if token is not None:
                snapshot_token.require_match(tracked.version, token, tracked.path)
            yield _Editing(session, tracked, resolved, context.subtask_id)
            completed = True
        finally:
            if not completed and owned:
                self._guard.release(resolved.path, context.main_task_id)

    def _verify(self, session: TaskSession, resolved: Resource, tracked: TrackedFile) -> None:
        """校验磁盘实际内容；不一致即停止本次动作，并按分区处置重置基底。"""
        assert resolved.path is not None
        if not tracked.exists:
            return
        try:
            check = check_version(resolved.path, tracked.h0_text)
        except FileNotFoundError as exc:
            raise FileError(f"{tracked.path} 已不存在；请重新 index 查看目录后再操作") from exc
        if check.ok:
            return
        h1 = check.h1_text or ""
        result = revalidate(session.queue, tracked.abs_path, tracked.h0_text, h1)
        session.refresh(tracked.abs_path, text=h1, exists=True)
        raise FileError(
            f"{tracked.path} 在读取之后已被修改（{describe_spans(check.spans)}）；"
            f"已作废 {len(result.invalidated)} 条相交条目、保留 {len(result.kept)} 条；"
            "请重新 read 后再提出"
        )

    def _checked_spans(
        self, tracked: TrackedFile, line_ranges: Sequence[Sequence[int]]
    ) -> list[LineRange]:
        spans = closed_ranges(line_ranges)
        limit = tracked.line_count + 1
        for span in spans:
            if span[1] > limit:
                raise FileError(
                    f"行号超出范围：{span[0]}-{span[1] - 1}，文件共 {tracked.line_count} 行"
                )
        return spans

    def _track(
        self, session: TaskSession, resolved: Resource, text: str, exists: bool
    ) -> TrackedFile:
        assert resolved.path is not None
        return session.track(
            resource=resolved.spec.id,
            path=self._display(resolved),
            abs_path=str(resolved.path.resolve()),
            text=text,
            exists=exists,
        )

    def _context(self) -> TaskContext:
        try:
            return current_task()
        except TaskContextError as exc:
            raise FileError(f"文件访问缺少任务身份：{exc}") from exc

    def _authorize(self, resource: Resource, action: str) -> TaskContext:
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

    def _display(self, resolved: Resource) -> str:
        spec = resolved.spec
        if resolved.path is None:
            return spec.label
        if spec.id == WORLD:
            if resolved.path == resolved.root:
                return "world"
            return relative_path(resolved.path, resolved.root)
        return resolved.path.name


def _resolver_world(resolver: ResourceResolver) -> Path:
    return resolver.resolve(WORLD).path  # type: ignore[return-value]


def _json(view: object) -> str:
    return json.dumps(view, ensure_ascii=False, indent=2)


def _closed(spans: Sequence[LineRange]) -> list[tuple[int, int]]:
    """把半开区间换成 `expect_matches` 使用的闭区间；`closed_ranges` 产物恒非零宽。"""
    return [(start, end - 1) for start, end in spans]


def _content_block(content: str) -> list[str]:
    normalized = normalize_text(content.encode("utf-8")).strip("\n")
    if not normalized.strip():
        raise FileError("内容不能为空")
    return normalized.split("\n")


def _proposed(tracked: TrackedFile, entry: str, lines: int, label: str) -> str:
    return (
        f"已提出，尚未写入文件；{label} {tracked.path}，产生 {entry}"
        f"（{lines} 行），等待 review 批准；本任务占用 {tracked.path}，提交或任务结束才释放"
    )


def _proposed_structure(resolved: Resource, entry: str, label: str) -> str:
    return f"已提出，尚未写入磁盘；{label}，产生 {entry}，等待 review 批准"


def _child_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.iterdir())
    except OSError:
        return 0


def _grep_lines(
    source: Sequence[str], regex, line_ranges: Sequence[Sequence[int]] | None, limit: int
) -> str:
    ranges = parse_ranges(line_ranges) if line_ranges else None
    matches: list[str] = []
    for number, line in enumerate(source, start=1):
        if ranges is not None and not any(start <= number <= end for start, end in ranges):
            continue
        if regex.search(line):
            matches.append(f"{number}: {line}")
            if len(matches) >= limit:
                return _grep_text(matches, True)
    return _grep_text(matches, False)


def _grep_text(matches: Sequence[str], truncated: bool) -> str:
    if not matches:
        return "没有匹配的内容"
    head = "\n".join(matches)
    if truncated:
        return f"{head}\n（已截断：仅显示前 {len(matches)} 条，可提高 limit 或缩小范围）"
    return head
