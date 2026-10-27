import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from infra.files.json_store import JsonDirectory, read_json
from infra.store.repositories.daily_log import DailyLogRecord
from service.memory.daily_events import event_text
from service.memory.daily_log_service import DailyLogService, SettingStore
from service.memory.sensitive import sensitive_reason

ID_PATTERN = r"^m_(\d{8})_(\d{3,})$"
ID_RE = re.compile(ID_PATTERN)
CATEGORIES = ("profile", "preference", "habit", "event", "interaction")
DEFAULT_IMPORTANCE = 3
SEQ_KEY_PREFIX = "memory.structured.seq."

Category = Literal["profile", "preference", "habit", "event", "interaction"]


class MemoryServiceError(Exception):
    code: str = "memory_error"


class MemoryNotFound(MemoryServiceError):
    code = "not_found"


class MemoryCorrupt(MemoryServiceError):
    code = "corrupt"


class MemorySource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["keyword", "explicit"]
    date: str

    @field_validator("date")
    @classmethod
    def _valid_date(cls, value: str) -> str:
        return date.fromisoformat(value).isoformat()


class StructuredMemory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=ID_PATTERN)
    category: Category
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    importance: int = Field(default=DEFAULT_IMPORTANCE, ge=1, le=5)
    source: MemorySource
    created_at: str
    updated_at: str

    @field_validator("created_at", "updated_at")
    @classmethod
    def _timezone_aware(cls, value: str) -> str:
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("时间必须带时区")
        return value


@dataclass(frozen=True)
class SkippedFile:
    name: str
    reason: str


@dataclass(frozen=True)
class MemoryListing:
    items: list[StructuredMemory] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)


class StructuredMemoryService:
    def __init__(
        self,
        files: JsonDirectory,
        settings: SettingStore,
        timezone: tzinfo,
        clock: Callable[[tzinfo], datetime] | None = None,
    ) -> None:
        self._files = files
        self._settings = settings
        self._timezone = timezone
        self._clock = clock or (lambda zone: datetime.now(zone))
        self._lock = threading.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._delete_listeners: list[Callable[[str], None]] = []

    def add_listener(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def add_delete_listener(self, listener: Callable[[str], None]) -> None:
        self._delete_listeners.append(listener)

    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    def _notify_deleted(self, memory_id: str) -> None:
        for listener in self._delete_listeners:
            listener(memory_id)

    def create(
        self,
        *,
        category: Category,
        title: str,
        content: str,
        source: MemorySource,
        importance: int = DEFAULT_IMPORTANCE,
    ) -> StructuredMemory:
        with self._lock:
            now = self._now()
            memory = StructuredMemory(
                id=self._allocate_id(now.date()),
                category=category,
                title=title,
                content=content,
                importance=importance,
                source=source,
                created_at=now.isoformat(timespec="seconds"),
                updated_at=now.isoformat(timespec="seconds"),
            )
            self._files.write(memory.id, memory.model_dump(mode="json"))
        self._notify()
        return memory

    def get(self, memory_id: str) -> StructuredMemory:
        if memory_id not in self._files.names():
            raise MemoryNotFound(f"结构化记忆 {memory_id} 不存在")
        return self._load(memory_id)

    def list(self, category: str | None = None) -> MemoryListing:
        listing = MemoryListing()
        for name in self._files.names():
            try:
                memory = self._load(name)
            except MemoryCorrupt as exc:
                listing.skipped.append(SkippedFile(name=name, reason=str(exc)))
                continue
            if category is None or memory.category == category:
                listing.items.append(memory)
        listing.items.sort(key=lambda item: (item.created_at, item.id))
        return listing

    def update(self, memory_id: str, **changes: Any) -> StructuredMemory:
        allowed = {"category", "title", "content", "importance"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不允许修改的字段：{sorted(unknown)}")
        with self._lock:
            current = self.get(memory_id)
            data = current.model_dump(mode="json")
            data.update(changes)
            data["updated_at"] = self._now().isoformat(timespec="seconds")
            try:
                updated = StructuredMemory.model_validate(data)
            except ValidationError as exc:
                raise ValueError(_first_error(exc)) from exc
            self._files.write(memory_id, updated.model_dump(mode="json"))
        self._notify()
        return updated

    def delete(self, memory_id: str) -> None:
        if not ID_RE.match(memory_id):
            raise ValueError(f"非法的记忆 id：{memory_id}")
        try:
            self._files.delete(memory_id)
        except FileNotFoundError as exc:
            raise MemoryNotFound(f"结构化记忆 {memory_id} 不存在") from exc
        self._notify_deleted(memory_id)
        self._notify()

    def _load(self, name: str) -> StructuredMemory:
        try:
            data = self._files.read(name)
        except (OSError, ValueError) as exc:
            raise MemoryCorrupt(f"无法读取：{exc}") from exc
        try:
            memory = StructuredMemory.model_validate(data)
        except ValidationError as exc:
            raise MemoryCorrupt(f"格式不正确：{_first_error(exc)}") from exc
        if memory.id != name:
            raise MemoryCorrupt(f"文件名 {name} 与记忆 id {memory.id} 不一致")
        return memory

    def _allocate_id(self, day: date) -> str:
        key = f"{SEQ_KEY_PREFIX}{day:%Y%m%d}"
        recorded = int(self._settings.get(key) or 0)
        existing = [
            int(match.group(2))
            for name in self._files.names()
            if (match := ID_RE.match(name)) and match.group(1) == f"{day:%Y%m%d}"
        ]
        sequence = max([recorded, *existing]) + 1
        self._settings.put(key, str(sequence))
        return f"m_{day:%Y%m%d}_{sequence:03d}"

    def _now(self) -> datetime:
        return self._clock(self._timezone)


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


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "(根)"
    return f"{location}: {error['msg']}"


KIND_STRUCTURED = "结构化记忆"
KIND_DAILY = "daily_memory"
RANGE_PATTERN = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


class MemoryReadError(MemoryServiceError):
    pass


def parse_range(spec: str) -> tuple[int, int]:
    match = RANGE_PATTERN.match(spec)
    if match is None:
        raise MemoryReadError(f"行号区间应为 from-to 形式，例如 1-5，当前为 {spec!r}")
    start, end = int(match.group(1)), int(match.group(2))
    if start < 1 or end < start:
        raise MemoryReadError(f"行号区间 {spec} 不合法：起始行须从 1 开始，且不大于结束行")
    return start, end


def number_lines(lines: Sequence[str], start: int = 1) -> str:
    return "\n".join(f"{number}: {line}" for number, line in enumerate(lines, start=start))


def section(lines: Sequence[str], spec: str) -> str:
    start, end = parse_range(spec)
    if end > len(lines):
        raise MemoryReadError(f"行号区间 {spec} 超出范围，共 {len(lines)} 行")
    return number_lines(lines[start - 1 : end], start)


def render_structured_lines(memory: StructuredMemory) -> list[str]:
    return [
        f"标题: {memory.title}",
        f"类别: {memory.category}",
        f"重要度: {memory.importance}",
        f"来源: {memory.source.type} {memory.source.date}",
        f"更新时间: {memory.updated_at}",
        "内容:",
        *memory.content.splitlines(),
    ]


class MemoryReader:
    def __init__(
        self,
        index_path: Path,
        structured: StructuredMemoryService,
        daily: DailyLogService,
    ) -> None:
        self._index_path = index_path
        self._structured = structured
        self._daily = daily

    def describe(self) -> dict[str, str]:
        return dict(self._index()["描述"])

    def index(self) -> dict[str, Any]:
        return self._index()["记忆索引"]

    def read(self, kind: str, entry_id: str, lines: str | None = None) -> str:
        source = self._lines_of(kind, entry_id)
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
        if not isinstance(data, dict) or "描述" not in data or "记忆索引" not in data:
            raise MemoryReadError("记忆索引格式不正确，请重启后端重新生成")
        return data

    def _lines_of(self, kind: str, entry_id: str) -> list[str]:
        if kind == KIND_STRUCTURED:
            try:
                memory = self._structured.get(entry_id)
            except (MemoryNotFound, MemoryCorrupt, ValueError) as exc:
                raise MemoryReadError(str(exc)) from exc
            return render_structured_lines(memory)
        if kind == KIND_DAILY:
            try:
                day = date.fromisoformat(entry_id).isoformat()
            except ValueError as exc:
                raise MemoryReadError(f"日期格式不合法：{entry_id}") from exc
            records = self._daily.read_day(day)
            if not records:
                raise MemoryReadError(f"{day} 没有对话记录")
            return render_daily_turns(records)
        raise MemoryReadError(f"未知的记忆类型：{kind}")


TRIGGERS = ("keyword", "explicit")


class MemoryWriteError(MemoryServiceError):
    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


class MemoryWriter:
    def __init__(
        self,
        memories: StructuredMemoryService,
        daily: DailyLogService,
        timezone: tzinfo,
        clock: Callable[[tzinfo], datetime] | None = None,
    ) -> None:
        self._memories = memories
        self._daily = daily
        self._timezone = timezone
        self._clock = clock or (lambda zone: datetime.now(zone))

    def remember(
        self,
        *,
        category: str,
        title: str,
        content: str,
        trigger: str = "keyword",
        importance: int = DEFAULT_IMPORTANCE,
    ) -> StructuredMemory:
        self._ensure_writable()
        if trigger not in TRIGGERS:
            raise MemoryWriteError(f"触发方式应为 keyword 或 explicit，当前为 {trigger!r}")
        self._ensure_not_sensitive(title, content)
        today = self._clock(self._timezone).date().isoformat()
        try:
            return self._memories.create(
                category=category,
                title=title,
                content=content,
                source=MemorySource(type=trigger, date=today),
                importance=importance,
            )
        except (MemoryServiceError, ValueError) as exc:
            raise MemoryWriteError(str(exc), code=getattr(exc, "code", "invalid")) from exc

    def revise(self, memory_id: str, **changes: Any) -> StructuredMemory:
        self._ensure_writable()
        for field_name in ("title", "content"):
            value = changes.get(field_name)
            if value is not None:
                self._ensure_not_sensitive(value)
        try:
            return self._memories.update(memory_id, **changes)
        except (MemoryServiceError, ValueError) as exc:
            raise MemoryWriteError(str(exc), code=getattr(exc, "code", "invalid")) from exc

    def temporary_mode(self) -> bool:
        return self._daily.temporary_mode()

    def _ensure_writable(self) -> None:
        if self._daily.temporary_mode():
            raise MemoryWriteError("临时模式开启，本次不写入结构化记忆", code="temporary_mode")

    def _ensure_not_sensitive(self, *texts: str) -> None:
        for text in texts:
            reason = sensitive_reason(text)
            if reason is not None:
                raise MemoryWriteError(f"拒绝写入：{reason}", code="sensitive")
