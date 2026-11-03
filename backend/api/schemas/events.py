from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from api.schemas.approval import ApprovalItem
from api.schemas.chat import ReplyMessageOut, SourceOut

EVENT_SCHEMA_NAME = "ServerEvent"


class StatusEvent(BaseModel):
    type: Literal["status"] = "status"
    state: Literal["idle", "thinking", "speaking", "executing", "sleeping"]


class ChatChunkEvent(BaseModel):
    type: Literal["chat.chunk"] = "chat.chunk"
    message_id: str
    text: str


class ChatDoneEvent(BaseModel):
    type: Literal["chat.done"] = "chat.done"
    message_id: str
    text: str
    sources: list[SourceOut] = Field(default_factory=list)
    messages: list[ReplyMessageOut] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class ApprovalRequestEvent(BaseModel):
    type: Literal["approval.request"] = "approval.request"
    plan_id: str
    items: list[ApprovalItem] = Field(default_factory=list)


class ProactiveMessageEvent(BaseModel):
    type: Literal["proactive.message"] = "proactive.message"
    id: str
    text: str
    topic: str
    at: datetime
    messages: list[ReplyMessageOut] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class UsageCallOut(BaseModel):
    """一次模型调用的明细；发起时尚无回复与 token，对应字段留空。

    正式模式下后端会清空 provider/model 与全部正文，只保留用途、token 与耗时。
    """

    provider: str | None = None
    model: str | None = None
    purpose: str | None = None
    turn_id: str | None = None
    run_id: str | None = None
    prompt_hash: str | None = None
    started_at: str
    finished_at: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    usage_raw: str | None = None
    latency_ms: int | None = None
    success: bool | None = None
    prompt_text: str | None = None
    reply_text: str | None = None
    reply_raw: str | None = None


class UsageCallStartedEvent(BaseModel):
    type: Literal["usage.call.started"] = "usage.call.started"
    call: UsageCallOut


class UsageCallFinishedEvent(BaseModel):
    type: Literal["usage.call.finished"] = "usage.call.finished"
    call: UsageCallOut


class UsageTurnStartedEvent(BaseModel):
    type: Literal["usage.turn.started"] = "usage.turn.started"
    turn_id: str
    at: str


class UsageTurnFinishedEvent(BaseModel):
    type: Literal["usage.turn.finished"] = "usage.turn.finished"
    turn_id: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_complete: bool = False
    model_time_ms: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    calls: list[UsageCallOut] = Field(default_factory=list)


class UsagePanelTurnOut(BaseModel):
    """状态窗口显示的当前轮：明细 + 汇总，已按模式脱敏。"""

    turn_id: str
    started_at: str
    finished_at: str | None = None
    finished: bool = False
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_complete: bool = False
    model_time_ms: int = 0
    calls: list[UsageCallOut] = Field(default_factory=list)


class UsagePanelSnapshotEvent(BaseModel):
    type: Literal["usage.panel.snapshot"] = "usage.panel.snapshot"
    mode: Literal["dev", "prod"]
    turn: UsagePanelTurnOut | None = None


ServerEvent = Annotated[
    StatusEvent
    | ChatChunkEvent
    | ChatDoneEvent
    | ApprovalRequestEvent
    | ProactiveMessageEvent
    | UsageCallStartedEvent
    | UsageCallFinishedEvent
    | UsageTurnStartedEvent
    | UsageTurnFinishedEvent
    | UsagePanelSnapshotEvent,
    Field(discriminator="type"),
]


def event_component_schemas() -> dict[str, Any]:
    schema = TypeAdapter(ServerEvent).json_schema(ref_template="#/components/schemas/{model}")
    definitions = schema.pop("$defs", {})
    return {**definitions, EVENT_SCHEMA_NAME: schema}
