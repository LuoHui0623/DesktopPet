from pydantic import BaseModel, ConfigDict, Field

from agent.memory.contracts import DailyLogView, MemoryEntryCreate, MemoryEntryUpdate


class MemoriesResponse(BaseModel):
    structured: str
    daily_memory: dict[str, list[str]]
    daily_records: list[DailyLogView] | None = None


class StructuredMemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class MemoryEntryView(BaseModel):
    """条目读回视图：供设置页编辑，携带 revision 作乐观锁；不含已弃用的 source_ref。"""

    id: int
    content: str
    tags: list[str]
    status_state: str
    status_reason: str | None = None
    revision: int
    created_at: str
    updated_at: str


class EntryListResponse(BaseModel):
    entries: list[MemoryEntryView]


class EntryAddResult(BaseModel):
    entry: MemoryEntryView
    reused: bool
    new_tags: list[str]


class EntryUpdateRequest(MemoryEntryUpdate):
    """人工修改请求：在 content/tags 之外必带预期 revision（Q27 乐观冲突检测）。"""

    expected_revision: int = Field(ge=1)


class EntryDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class TagListResponse(BaseModel):
    tags: list[str]


class TagMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str


class TagPathChangeView(BaseModel):
    """单条条目合并/改名前后的有序路径（Q27/第 29 条预览）。"""

    entry_id: int
    content: str
    before: list[str]
    after: list[str]


class TagMergePreviewResponse(BaseModel):
    source: str
    target: str
    target_existed: bool
    affected: int
    changes: list[TagPathChangeView]


class TagMergeResponse(BaseModel):
    source: str
    target: str
    affected: int
    target_existed: bool


# 新增条目沿用 AI 写入同一契约（Q8：人工与 AI 同一写路径与校验）。
EntryCreateRequest = MemoryEntryCreate
