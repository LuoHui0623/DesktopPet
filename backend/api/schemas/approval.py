from pydantic import BaseModel, ConfigDict, Field


class PreviewHunk(BaseModel):
    header: str
    removed: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)


class FilePreview(BaseModel):
    resource: str
    path: str
    op: str
    created: bool = False
    deleted: bool = False
    authors: list[str] = Field(default_factory=list)
    hunk_count: int = 0
    folded: bool = False
    hunks: list[PreviewHunk] = Field(default_factory=list)


class ApprovalItem(BaseModel):
    approval_id: str
    run_id: str
    topic: str
    task: str
    content: str
    tools: list[str] = Field(default_factory=list)
    persona_id: str | None = None
    persona_version: int | None = None
    main_task_id: str | None = None
    subtask_id: str | None = None
    source: str | None = None
    previews: list[FilePreview] = Field(default_factory=list)
    model: str | None = None
    # 读取票据额外携带：动作、目标与参数摘要；结算票据保持为空。
    action: str | None = None
    resource: str | None = None
    path: str | None = None
    parameters: str | None = None
    plan_id: str | None = None


class ApprovalList(BaseModel):
    items: list[ApprovalItem] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    approved: bool


class ApprovalAck(BaseModel):
    approval_id: str
    approved: bool
