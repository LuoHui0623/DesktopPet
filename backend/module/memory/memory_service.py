import itertools
import re
import threading
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from infra.files.json_store import read_json
from module.memory.contracts import DailyLogEntry
from module.memory.daily_events import event_text
from module.memory.daily_log_service import DailyLogService
from module.memory.sensitive import sensitive_reason

STRUCTURED_DOC_NAME = "structured"
STRUCTURED_FILE_NAME = "structured.md"
STRUCTURED_ALIASES = (STRUCTURED_DOC_NAME, STRUCTURED_FILE_NAME)
KIND_STRUCTURED = "structured"
KIND_DAILY = "daily"
KIND_LABELS = {KIND_STRUCTURED: "结构化记忆", KIND_DAILY: "每日对话"}

LINE_RANGE_HINT = "行号位置应为 [[起始行, 结束行], ...]，单行写 [行号]"


class MemoryServiceError(Exception):
    code: str = "memory_error"


class MemoryReadError(MemoryServiceError):
    code = "read_error"


class MemoryWriteError(MemoryServiceError):
    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def parse_ranges(specs: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for spec in specs:
        if isinstance(spec, str):
            raise MemoryReadError(f"行号位置 {spec!r} 不合法：{LINE_RANGE_HINT}")
        values = [int(value) for value in spec]
        if not 1 <= len(values) <= 2:
            raise MemoryReadError(f"行号位置 {values} 不合法：{LINE_RANGE_HINT}")
        start = values[0]
        end = values[1] if len(values) == 2 else start
        if start < 1 or end < start:
            raise MemoryReadError(f"行号位置 {values} 不合法：起始行须从 1 开始，且不大于结束行")
        ranges.append((start, end))
    if not ranges:
        raise MemoryReadError(f"行号位置不能为空：{LINE_RANGE_HINT}")
    ranges.sort()
    for previous, current in itertools.pairwise(ranges):
        if current[0] <= previous[1]:
            raise MemoryReadError(
                f"行号位置 {previous[0]}-{previous[1]} 与 {current[0]}-{current[1]} 重叠"
            )
    return ranges


def number_lines(lines: Sequence[str], start: int = 1) -> str:
    return "\n".join(f"{number}: {line}" for number, line in enumerate(lines, start=start))


def checked_ranges(lines: Sequence[str], specs: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    ranges = parse_ranges(specs)
    for start, end in ranges:
        if end > len(lines):
            raise MemoryReadError(f"行号位置 {start}-{end} 超出范围，共 {len(lines)} 行")
    return ranges


def expect_matches(
    lines: Sequence[str],
    ranges: Sequence[tuple[int, int]],
    expect: str | Sequence[str] | None,
) -> None:
    """可选的冲突检测：调用方给出目标行原文，不一致时不写入。"""
    if expect is None:
        return
    expected = [expect] if isinstance(expect, str) else list(expect)
    if len(expected) != len(ranges):
        raise MemoryWriteError(f"expect 需要提供 {len(ranges)} 段原文，实际 {len(expected)} 段")
    for (start, end), current in zip(ranges, expected, strict=True):
        actual = "\n".join(lines[start - 1 : end])
        if actual != current:
            raise MemoryWriteError(
                f"第 {start}-{end} 行与 expect 不一致，文件可能已被修改；请先用 read 核对"
            )


def replace_ranges(
    lines: Sequence[str],
    ranges: Sequence[tuple[int, int]],
    block: Sequence[str],
) -> list[str]:
    """按行区间原地替换，返回新行列表；区间不可重叠。"""
    updated = list(lines)
    offset = 0
    for start, end in ranges:
        position = start - 1 + offset
        count = end - start + 1
        updated[position : position + count] = block
        offset += len(block) - count
    return updated


def delete_ranges(lines: Sequence[str], ranges: Sequence[tuple[int, int]]) -> list[str]:
    updated = list(lines)
    for start, end in reversed(ranges):
        del updated[start - 1 : end]
    return updated


def section(lines: Sequence[str], specs: Sequence[Sequence[int]]) -> str:
    ranges = checked_ranges(lines, specs)
    return "\n".join(number_lines(lines[start - 1 : end], start) for start, end in ranges)


def split_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return []
    return normalized.rstrip("\n").split("\n")


def _content_lines(content: str) -> list[str]:
    if not content.strip():
        raise MemoryWriteError("内容不能为空")
    return _flatten_newlines(content).strip("\n").split("\n")


def _flatten_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def compile_pattern(pattern: str) -> re.Pattern[str]:
    if not pattern.strip():
        raise MemoryWriteError("正则不能为空")
    try:
        regex = re.compile(pattern, re.MULTILINE)
    except re.error as exc:
        raise MemoryWriteError(f"正则不合法：{exc}") from exc
    if regex.search("") is not None:
        raise MemoryWriteError(f"正则 {pattern!r} 能匹配空字符串，请写出要替换的具体内容")
    return regex


class StructuredMemoryDoc:
    """结构化记忆是单个纯文本文件：不规定结构，由 agent 自行设计，按行号定位与修改。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    @property
    def path(self) -> Path:
        return self._path

    def text(self) -> str:
        self._create_if_missing()
        return self._path.read_text(encoding="utf-8")

    def _create_if_missing(self) -> None:
        if self._path.exists():
            return
        with self._lock:
            if self._path.exists():
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("", encoding="utf-8")

    def lines(self) -> list[str]:
        return split_lines(self.text())

    def line_count(self) -> int:
        return len(self.lines())

    def save(self, text: str) -> None:
        normalized = _flatten_newlines(text)
        body = normalized.rstrip("\n")
        body = f"{body}\n" if body.strip() else ""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_name(f"{self._path.name}.tmp")
            temporary.write_text(body, encoding="utf-8")
            temporary.replace(self._path)
        self._notify()

    def append(self, content: str) -> tuple[int, int]:
        block = _content_lines(content)
        lines = self.lines()
        start = len(lines) + 1
        self.save("\n".join([*lines, *block]))
        return start, start + len(block) - 1

    def insert(self, line: int, content: str) -> tuple[int, int]:
        block = _content_lines(content)
        lines = self.lines()
        if not 1 <= line <= len(lines) + 1:
            raise MemoryWriteError(f"插入位置 {line} 超出范围：应为 1–{len(lines) + 1}")
        updated = [*lines[: line - 1], *block, *lines[line - 1 :]]
        self.save("\n".join(updated))
        return line, line + len(block) - 1

    def edit(
        self,
        specs: Sequence[Sequence[int]],
        content: str,
        expect: str | Sequence[str] | None = None,
    ) -> tuple[int, int]:
        block = _content_lines(content)
        lines = self.lines()
        try:
            ranges = checked_ranges(lines, specs)
        except MemoryReadError as exc:
            raise MemoryWriteError(str(exc)) from exc
        expect_matches(lines, ranges, expect)
        updated = replace_ranges(lines, ranges, block)
        self.save("\n".join(updated))
        shift = sum(len(block) - (end - start + 1) for start, end in ranges[:-1])
        start = ranges[0][0]
        last = ranges[-1][0] + shift
        return start, last + len(block) - 1

    def delete(
        self,
        specs: Sequence[Sequence[int]],
        expect: str | Sequence[str] | None = None,
    ) -> None:
        if not specs:
            raise MemoryWriteError("删除内容需要提供行号位置")
        lines = self.lines()
        try:
            ranges = checked_ranges(lines, specs)
        except MemoryReadError as exc:
            raise MemoryWriteError(str(exc)) from exc
        expect_matches(lines, ranges, expect)
        self.save("\n".join(delete_ranges(lines, ranges)))

    def replace_pattern(
        self, specs: Sequence[Sequence[int]], pattern: str, content: str
    ) -> tuple[int, int]:
        replacement = "\n".join(_content_lines(content))
        regex = compile_pattern(pattern)
        lines = self.lines()
        try:
            ranges = checked_ranges(lines, specs)
        except MemoryReadError as exc:
            raise MemoryWriteError(str(exc)) from exc
        updated = list(lines)
        hits = 0
        first = len(lines) + 1
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
        if not hits:
            raise MemoryWriteError(
                f"正则 {pattern!r} 在指定行内没有匹配到内容，请先用 read 确认原文再改"
            )
        self.save("\n".join(updated))
        return first, last


def render_daily_turns(records: Sequence[DailyLogEntry]) -> list[str]:
    turns: list[list[DailyLogEntry]] = []
    for record in records:
        if record.role == "master" or not turns:
            turns.append([record])
        else:
            turns[-1].append(record)
    return [" / ".join(_render_daily_line(record) for record in turn) for turn in turns]


def _render_daily_line(record: DailyLogEntry) -> str:
    if record.role == "event":
        return f"事件：{_flatten(event_text(record.content))}"
    labels = {"master": "用户", "me": "宠物"}
    return f"{labels.get(record.role, record.role)}：{_flatten(record.content)}"


def _flatten(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", " ")


class MemoryReader:
    def __init__(
        self,
        index_path: Path,
        structured: StructuredMemoryDoc,
        daily: DailyLogService,
    ) -> None:
        self._index_path = index_path
        self._structured = structured
        self._daily = daily

    def describe(self) -> dict[str, str]:
        return dict(self._index()["descriptions"])

    def index(self) -> dict[str, Any]:
        return self._index()["index"]

    def read(self, kind: str, name: str, lines: Sequence[Sequence[int]] | None = None) -> str:
        source = self._lines_of(kind, name)
        if not lines:
            return number_lines(source)
        return section(source, lines)

    def _index(self) -> dict[str, Any]:
        try:
            data = read_json(self._index_path)
        except FileNotFoundError as exc:
            raise MemoryReadError("记忆索引不存在，请重启后端生成") from exc
        except (OSError, ValueError) as exc:
            raise MemoryReadError(f"记忆索引无法读取：{exc}") from exc
        if not isinstance(data, dict) or "descriptions" not in data or "index" not in data:
            raise MemoryReadError("记忆索引格式不正确，请重启后端重新生成")
        return data

    def _lines_of(self, kind: str, name: str) -> list[str]:
        if kind == KIND_STRUCTURED:
            return self._structured.lines()
        if kind == KIND_DAILY:
            try:
                day = date.fromisoformat(name).isoformat()
            except ValueError as exc:
                raise MemoryReadError(f"日期格式不合法：{name}") from exc
            records = self._daily.read_day(day)
            if not records:
                raise MemoryReadError(f"{day} 没有对话记录")
            return render_daily_turns(records)
        raise MemoryReadError(f"未知的记忆类型：{kind}")


class MemoryWriter:
    def __init__(self, structured: StructuredMemoryDoc) -> None:
        self._structured = structured

    def append(self, content: str) -> tuple[int, int]:
        self._ensure_not_sensitive(content)
        return self._structured.append(content)

    def insert(self, line: int, content: str) -> tuple[int, int]:
        self._ensure_not_sensitive(content)
        return self._structured.insert(line, content)

    def edit(
        self,
        lines: Sequence[Sequence[int]],
        content: str,
        expect: str | Sequence[str] | None = None,
    ) -> tuple[int, int]:
        self._ensure_not_sensitive(content)
        return self._structured.edit(lines, content, expect)

    def delete(
        self,
        lines: Sequence[Sequence[int]],
        expect: str | Sequence[str] | None = None,
    ) -> None:
        self._structured.delete(lines, expect)

    def replace_pattern(
        self, lines: Sequence[Sequence[int]], pattern: str, content: str
    ) -> tuple[int, int]:
        self._ensure_not_sensitive(content)
        return self._structured.replace_pattern(lines, pattern, content)

    def _ensure_not_sensitive(self, text: str) -> None:
        reason = sensitive_reason(text)
        if reason is not None:
            raise MemoryWriteError(f"拒绝写入：{reason}", code="sensitive")
