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


ServerEvent = Annotated[
    StatusEvent | ChatChunkEvent | ChatDoneEvent | ApprovalRequestEvent | ProactiveMessageEvent,
    Field(discriminator="type"),
]


def event_component_schemas() -> dict[str, Any]:
    schema = TypeAdapter(ServerEvent).json_schema(ref_template="#/components/schemas/{model}")
    definitions = schema.pop("$defs", {})
    return {**definitions, EVENT_SCHEMA_NAME: schema}
