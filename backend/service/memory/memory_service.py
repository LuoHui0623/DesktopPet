import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from infra.files.json_store import JsonDirectory
from infra.store.repositories.daily_log import DailyLogRecord
from service.memory.daily_log_service import SettingStore

ID_PATTERN = r"^m_(\d{8})_(\d{3,})$"
ID_RE = re.compile(ID_PATTERN)
CATEGORIES = ("profile", "preference", "habit", "event", "interaction")
DEFAULT_IMPORTANCE = 3
SEQ_KEY_PREFIX = "memory.structured.seq."

Category = Literal["profile", "preference", "habit", "event", "interaction"]


class MemoryServiceError(Exception):
    pass


class MemoryNotFound(MemoryServiceError):
    pass


class MemoryCorrupt(MemoryServiceError):
    pass


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
            return updated

    def delete(self, memory_id: str) -> None:
        if not ID_RE.match(memory_id):
            raise ValueError(f"非法的记忆 id：{memory_id}")
        try:
            self._files.delete(memory_id)
        except FileNotFoundError as exc:
            raise MemoryNotFound(f"结构化记忆 {memory_id} 不存在") from exc

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
        if record.role == "user" or not turns:
            turns.append([record])
        else:
            turns[-1].append(record)
    labels = {"user": "用户", "assistant": "宠物"}
    return [
        " / ".join(
            f"{labels.get(record.role, record.role)}：{_flatten(record.content)}" for record in turn
        )
        for turn in turns
    ]


def _flatten(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", " ")


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "(根)"
    return f"{location}: {error['msg']}"
