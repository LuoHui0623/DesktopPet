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
    ACTION_EDIT,
    ACTION_GREP,
    ACTION_INDEX,
    ACTION_INSERT,
    ACTION_READ,
    ACTION_REPLACE,
    PERMANENT,
    RESOURCE_ORDER,
    RESOURCES,
    STRUCTURED,
    WORLD,
)
from infra.files import io
from infra.llm.tokens import TokenCounter, TokenCountError, create_counter
from module.agent.skills import SkillRegistry
from module.files.resolver import FileError, Resource, ResourceResolver
from module.memory.daily_log_service import DailyLogService
from module.memory.index import structured_index
from module.memory.memory_service import (
    MemoryReadError,
    MemoryWriteError,
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

ACTION_DOCS = {
    ACTION_INDEX: ("列出资源、路径与权限；resource 为 world 时列出目录树，为 skill 时列出能力清单"),
    ACTION_READ: (
        "读取文本并附行号（从 1 开始）；lines 为 [[起, 止], ...]；"
        "永久记忆用 date 或 from/to 指定时间范围"
    ),
    ACTION_GREP: (
        "按 Python 正则检索；world 未给 path 时递归检索目录；永久记忆用 date 或 from/to 限定范围"
    ),
    ACTION_EDIT: ("用 content 替换 lines 指定的行；expect 可给出目标行原文，用于冲突检测"),
    ACTION_APPEND: "追加到文件末尾；world 中不存在的路径会新建文件",
    ACTION_INSERT: "在 line 之前插入 content，line 取 1 到 行数+1",
    ACTION_REPLACE: (
        "在 lines（省略则全文）内用正则匹配，把匹配文字替换为 content；没有匹配时报错"
    ),
    ACTION_DELETE: (
        "删除 lines 指定的行并保留文件；省略 lines 时只有 world 能删除文件本体，"
        "草稿本与结构化记忆不能删除文件"
    ),
}

PARAMETER_DOCS = {
    "resource": "资源 ID：draft、structured、permanent、world；index 与 read 还接受 skill",
    "path": "world 内的相对路径；draft / structured 只接受自己的文件名或等价路径",
    "lines": "行号位置 [[起, 止], ...]，从 1 开始并包含两端；按动作语义可省略",
    "content": "字面文本，不解析反斜杠转义；append / insert / edit 不能为空",
    "expect": "可选冲突检测：目标行原文，可传字符串或与 lines 等长的列表",
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
        timezone: tzinfo,
        limit_provider: Callable[[], ModelLimit],
        clock: Callable[[tzinfo], datetime] | None = None,
        counter_factory: Callable[[str], TokenCounter] | None = None,
    ) -> None:
        self._resolver = resolver
        self._structured = structured
        self._daily = daily
        self._skills = skills
        self._timezone = timezone
        self._clock = clock or _system_now
        self._limit_provider = limit_provider
        self._counter_factory = counter_factory or create_counter
        self._counters: dict[str, TokenCounter] = {}
        self._counter_lock = threading.Lock()
        self._locks: dict[Path, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def index(self, resource: str | None = None, path: str | None = None) -> str:
        if resource == "skill":
            if path is not None:
                raise FileError("技能清单不需要 path")
            return _json(self._skill_catalog())
        if resource is None and path is None:
            return _json(self._overview())
        resolved = self._resolver.resolve(resource, path)
        if resolved.spec.id == PERMANENT:
            return _json(self._permanent_index())
        assert resolved.path is not None
        if resolved.path.is_dir():
            return _json(self._tree(resolved))
        return _json(self._file_index(resolved))

    def read(
        self,
        resource: str | None = None,
        path: str | None = None,
        name: str | None = None,
        lines: Sequence[Sequence[int]] | None = None,
        day: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
    ) -> str:
        if resource == "skill":
            if not name:
                raise FileError("读取技能说明书需要 name；先用 index（resource 为 skill）查看清单")
            return self._skills.read(name)
        resolved = self._resolver.resolve(resource, path)
        if resolved.spec.id == PERMANENT:
            return self._read_permanent(lines, day=day, start_at=start_at, end_at=end_at)
        return self._read_file(resolved, lines)

    def grep(
        self,
        pattern: str,
        resource: str | None = None,
        path: str | None = None,
        lines: Sequence[Sequence[int]] | None = None,
        day: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        limit: int = GREP_LIMIT,
    ) -> str:
        regex = compile_pattern(pattern)
        resolved = self._resolver.resolve(resource, path)
        if resolved.spec.id == PERMANENT:
            source = self._permanent_lines(day=day, start_at=start_at, end_at=end_at)
            content = _grep_lines(source, regex, lines, limit)
            self._enforce_limit(content, "永久记忆检索")
            return content
        assert resolved.path is not None
        if resolved.path.is_dir():
            return self._grep_tree(resolved, regex, limit)
        try:
            source = _lines_of(resolved.path)
        except FileNotFoundError as exc:
            raise FileError(f"文件不存在：{resolved.path.name}") from exc
        except UnicodeDecodeError as exc:
            raise FileError(f"不是 UTF-8 文本：{resolved.path.name}") from exc
        return _grep_lines(source, regex, lines, limit)

    def append(self, resource: str, content: str, path: str | None = None) -> str:
        resolved = self._writable(resource, path, ACTION_APPEND)
        block = _content_block(content)
        if resolved.spec.id == STRUCTURED:
            start, end = self._writer().append(content)
            return f"已追加，位于第 {start}-{end} 行"
        assert resolved.path is not None
        if resolved.path.is_dir():
            raise FileError("目标是目录，请指定文件")
        with self._lock(resolved.path):
            if resolved.path.exists():
                lines, trailing = _read_lines(resolved.path)
            elif resolved.spec.id == WORLD:
                lines, trailing = [], True
            else:
                raise FileError(f"文件不存在：{resolved.path.name}")
            lines = [*lines, *block]
            start = len(lines) - len(block) + 1
            _write_lines(resolved.path, lines, trailing)
        return f"已追加，位于第 {start}-{start + len(block) - 1} 行"

    def insert(self, resource: str, line: int, content: str, path: str | None = None) -> str:
        resolved = self._writable(resource, path, ACTION_INSERT)
        block = _content_block(content)
        if resolved.spec.id == STRUCTURED:
            start, end = self._writer().insert(line, content)
            return f"已插入，位于第 {start}-{end} 行"
        assert resolved.path is not None
        with self._lock(resolved.path):
            lines, trailing = _read_lines(_require_file(resolved.path))
            if not 1 <= line <= len(lines) + 1:
                raise FileError(f"插入位置 {line} 超出范围：应为 1–{len(lines) + 1}")
            lines = [*lines[: line - 1], *block, *lines[line - 1 :]]
            _write_lines(resolved.path, lines, trailing)
        return f"已插入，位于第 {line}-{line + len(block) - 1} 行"

    def edit(
        self,
        resource: str,
        lines: Sequence[Sequence[int]],
        content: str,
        path: str | None = None,
        expect: str | Sequence[str] | None = None,
    ) -> str:
        if not lines:
            raise FileError("编辑需要提供行号位置 lines；删除整段内容请用 delete")
        resolved = self._writable(resource, path, ACTION_EDIT)
        if resolved.spec.id == STRUCTURED:
            start, end = self._writer().edit(lines, content, expect)
            return f"已编辑，位于第 {start}-{end} 行"
        block = _content_block(content)
        assert resolved.path is not None
        with self._lock(resolved.path):
            source, trailing = _read_lines(_require_file(resolved.path))
            ranges = _checked_ranges(source, lines)
            expect_matches(source, ranges, expect)
            updated = replace_ranges(source, ranges, block)
            _write_lines(resolved.path, updated, trailing)
        shift = sum(len(block) - (end - start + 1) for start, end in ranges[:-1])
        return f"已编辑，位于第 {ranges[0][0]}-{ranges[-1][0] + shift + len(block) - 1} 行"

    def replace(
        self,
        resource: str,
        pattern: str,
        content: str,
        path: str | None = None,
        lines: Sequence[Sequence[int]] | None = None,
    ) -> str:
        resolved = self._writable(resource, path, ACTION_REPLACE)
        regex = compile_pattern(pattern)
        replacement = "\n".join(_content_block(content))
        if resolved.spec.id == STRUCTURED:
            if not lines:
                total = len(self._structured.lines())
                if total == 0:
                    raise MemoryWriteError(f"正则 {pattern!r} 在文件内没有匹配到内容")
                lines = [[1, total]]
            start, end = self._writer().replace_pattern(lines, pattern, content)
            return f"已替换，位于第 {start}-{end} 行"
        assert resolved.path is not None
        with self._lock(resolved.path):
            source, trailing = _read_lines(_require_file(resolved.path))
            ranges = _checked_ranges(source, lines) if lines else _whole_file(source)
            updated, hits, first, last = _replace_ranges(source, ranges, regex, replacement)
            if not hits:
                raise FileError(f"正则 {pattern!r} 没有匹配到内容，请先用 read 确认原文")
            _write_lines(resolved.path, updated, trailing)
        return f"已替换，共 {hits} 处，位于第 {first}-{last} 行"

    def delete(
        self,
        resource: str,
        path: str | None = None,
        lines: Sequence[Sequence[int]] | None = None,
        expect: str | Sequence[str] | None = None,
    ) -> str:
        resolved = self._writable(resource, path, ACTION_DELETE)
        if resolved.spec.id == STRUCTURED:
            if not lines:
                raise FileError("结构化记忆不能删除文件本体；请提供 lines 删除内容")
            self._writer().delete(lines, expect)
            return f"已删除第 {_span_text(lines)} 行"
        assert resolved.path is not None
        if resolved.path.is_dir():
            if resolved.spec.id == WORLD and lines is None:
                raise FileError("不能删除小世界目录；只允许删除文件")
            raise FileError("目标是目录，请指定文件")
        if lines is None:
            if not resolved.spec.can_delete_file:
                raise FileError(f"{resolved.spec.label}不能删除文件本体；请提供 lines 只删除内容")
            with self._lock(resolved.path):
                _require_file(resolved.path)
                resolved.path.unlink()
            return f"已删除文件：{_display(resolved)}"
        with self._lock(resolved.path):
            source, trailing = _read_lines(_require_file(resolved.path))
            ranges = _checked_ranges(source, lines)
            expect_matches(source, ranges, expect)
            for start, end in reversed(ranges):
                del source[start - 1 : end]
            _write_lines(resolved.path, source, trailing)
        return f"已删除第 {_span_text(lines)} 行"

    def _overview(self) -> dict[str, object]:
        return {
            "persona": self._daily.active_persona(),
            "resources": [
                {
                    "id": resource_id,
                    "label": spec.label,
                    "path": spec.path,
                    "actions": sorted(spec.actions),
                    "can_delete_file": spec.can_delete_file,
                    "description": spec.description,
                }
                for resource_id in RESOURCE_ORDER
                for spec in [RESOURCES[resource_id]]
            ],
            "actions": ACTION_DOCS,
            "parameters": PARAMETER_DOCS,
            "limits": LIMIT_DOCS,
            "skills": self._skill_catalog(),
        }

    def _skill_catalog(self) -> list[dict[str, str]]:
        return [
            {"name": item.name, "description": item.description} for item in self._skills.index()
        ]

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
            "resource": PERMANENT,
            "label": RESOURCES[PERMANENT].label,
            "persona": self._daily.active_persona(),
            "dates": self._daily.dates(),
            "actions": sorted(RESOURCES[PERMANENT].actions),
            "limit": limit_payload,
            "description": RESOURCES[PERMANENT].description,
        }

    def _file_index(self, resource: Resource) -> dict[str, object]:
        assert resource.path is not None
        source, _ = _read_lines(resource.path)
        payload: dict[str, object] = {
            "resource": resource.spec.id,
            "label": resource.spec.label,
            "path": _display(resource),
            "lines": len(source),
            "actions": sorted(resource.spec.actions),
            "can_delete_file": resource.spec.can_delete_file,
        }
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
                entries.append({"path": _relative(Path(current) / name, root), "type": "directory"})
            for name in sorted(files):
                target = Path(current) / name
                if _is_link(target):
                    entries.append({"path": _relative(target, root), "type": "link"})
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
            "resource": WORLD,
            "path": _display(resource),
            "entries": entries,
            "truncated": truncated,
        }

    def _read_file(self, resource: Resource, lines: Sequence[Sequence[int]] | None) -> str:
        assert resource.path is not None
        if resource.path.is_dir():
            raise FileError("目标是目录，请指定具体文件；用 index 查看目录内容")
        source, _ = _read_lines(_require_file(resource.path))
        if not lines:
            return number_lines(source)
        try:
            return section(source, lines)
        except MemoryReadError as exc:
            raise FileError(str(exc)) from exc

    def _read_permanent(
        self,
        lines: Sequence[Sequence[int]] | None,
        *,
        day: str | None,
        start_at: str | None,
        end_at: str | None,
    ) -> str:
        source = self._permanent_lines(day=day, start_at=start_at, end_at=end_at)
        try:
            content = section(source, lines) if lines else number_lines(source)
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
        persona = self._daily.active_persona()
        if day is not None:
            if start_at is not None or end_at is not None:
                raise FileError("date 与 from/to 不能同时使用")
            views = self._daily.day_views(_day_text(day))
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
        matches: list[str] = []
        truncated = False
        for current, directories, files in os.walk(root):
            directories[:] = [
                name for name in sorted(directories) if not _is_link(Path(current) / name)
            ]
            for name in sorted(files):
                target = Path(current) / name
                if _is_link(target):
                    continue
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
        return _grep_text(matches, truncated)

    def _writer(self) -> MemoryWriter:
        return MemoryWriter(self._structured)

    def _writable(self, resource: str, path: str | None, action: str) -> Resource:
        resolved = self._resolver.resolve(resource, path)
        self._resolver.require(resolved, action)
        if resolved.path is None:
            raise FileError(f"{resolved.spec.label}没有可写入的文件")
        return resolved

    def _lock(self, path: Path) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(path)
            if lock is None:
                lock = threading.Lock()
                self._locks[path] = lock
            return lock


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _content_block(content: str) -> list[str]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not normalized.strip():
        raise FileError("内容不能为空")
    return normalized.split("\n")


def _lines_of(path: Path) -> list[str]:
    return split_lines(io.read_text(path))


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


def _whole_file(source: Sequence[str]) -> list[tuple[int, int]]:
    if not source:
        raise FileError("文件是空的，没有可替换的内容")
    return [(1, len(source))]


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


def _display(resource: Resource) -> str:
    if resource.spec.id == WORLD:
        return "resources"
    assert resource.path is not None
    return resource.path.name


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _span_text(lines: Sequence[Sequence[int]]) -> str:
    ranges = parse_ranges(lines)
    return "、".join(f"{start}" if start == end else f"{start}-{end}" for start, end in ranges)
