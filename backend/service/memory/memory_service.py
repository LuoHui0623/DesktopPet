import re
import threading
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from infra.files.json_store import read_json
from infra.store.repositories.daily_log import DailyLogRecord
from service.memory.daily_events import event_text
from service.memory.daily_log_service import DailyLogService
from service.memory.sensitive import sensitive_reason

STRUCTURED_DOC_NAME = "structured"
STRUCTURED_FILE_NAME = "structured.md"
STRUCTURED_ALIASES = (STRUCTURED_DOC_NAME, STRUCTURED_FILE_NAME)
DEFAULT_STRUCTURED_DOC = """## 主人信息
## 喜好
## 作息与习惯
## 约定
"""
KIND_STRUCTURED = "structured"
KIND_DAILY = "daily"
KIND_LABELS = {KIND_STRUCTURED: "结构化记忆", KIND_DAILY: "每日对话"}

RANGE_PATTERN = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


class MemoryServiceError(Exception):
    code: str = "memory_error"


class MemoryReadError(MemoryServiceError):
    code = "read_error"


class MemoryWriteError(MemoryServiceError):
    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def parse_range(spec: str) -> tuple[int, int]:
    match = RANGE_PATTERN.match(spec)
    if match is None:
        raise MemoryReadError(
            f"行号应为 from-to 形式（例如 1-5）或单个行号（例如 3），当前为 {spec!r}"
        )
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) is not None else start
    if start < 1 or end < start:
        raise MemoryReadError(f"行号区间 {spec} 不合法：起始行须从 1 开始，且不大于结束行")
    return start, end


def number_lines(lines: Sequence[str], start: int = 1) -> str:
    return "\n".join(f"{number}: {line}" for number, line in enumerate(lines, start=start))


def checked_range(lines: Sequence[str], spec: str) -> tuple[int, int]:
    start, end = parse_range(spec)
    if end > len(lines):
        raise MemoryReadError(f"行号区间 {spec} 超出范围，共 {len(lines)} 行")
    return start, end


def section(lines: Sequence[str], spec: str) -> str:
    start, end = checked_range(lines, spec)
    return number_lines(lines[start - 1 : end], start)


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


class StructuredMemoryDoc:
    """结构化记忆是单个纯文本大纲文档：不校验语法，按行号定位与修改。"""

    def __init__(self, path: Path, template: str = DEFAULT_STRUCTURED_DOC) -> None:
        self._path = path
        self._template = template
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
        try:
            return self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.save(self._template)
            return self.text()

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

    def replace_lines(self, spec: str, content: str) -> tuple[int, int]:
        block = _content_lines(content)
        try:
            start, end = checked_range(self.lines(), spec)
        except MemoryReadError as exc:
            raise MemoryWriteError(str(exc)) from exc
        lines = self.lines()
        self.save("\n".join([*lines[: start - 1], *block, *lines[end:]]))
        return start, start + len(block) - 1


def render_daily_turns(records: Sequence[DailyLogRecord]) -> list[str]:
    turns: list[list[DailyLogRecord]] = []
    for record in records:
        if record.role == "master" or not turns:
            turns.append([record])
        else:
            turns[-1].append(record)
    return [" / ".join(_render_daily_line(record) for record in turn) for turn in turns]


def _render_daily_line(record: DailyLogRecord) -> str:
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

    def read(self, kind: str, name: str, lines: str | None = None) -> str:
        source = self._lines_of(kind, name)
        if lines is None:
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
    def __init__(self, structured: StructuredMemoryDoc, daily: DailyLogService) -> None:
        self._structured = structured
        self._daily = daily

    def append(self, content: str) -> tuple[int, int]:
        self._ensure_writable()
        self._ensure_not_sensitive(content)
        return self._structured.append(content)

    def replace_lines(self, lines: str, content: str) -> tuple[int, int]:
        self._ensure_writable()
        self._ensure_not_sensitive(content)
        return self._structured.replace_lines(lines, content)

    def temporary_mode(self) -> bool:
        return self._daily.temporary_mode()

    def _ensure_writable(self) -> None:
        if self._daily.temporary_mode():
            raise MemoryWriteError("临时模式开启，本次不写入结构化记忆", code="temporary_mode")

    def _ensure_not_sensitive(self, text: str) -> None:
        reason = sensitive_reason(text)
        if reason is not None:
            raise MemoryWriteError(f"拒绝写入：{reason}", code="sensitive")
