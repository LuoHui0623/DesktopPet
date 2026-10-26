from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

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


ServerEvent = Annotated[
    StatusEvent | ChatChunkEvent | ChatDoneEvent,
    Field(discriminator="type"),
]


def event_component_schemas() -> dict[str, Any]:
    schema = TypeAdapter(ServerEvent).json_schema(ref_template="#/components/schemas/{model}")
    definitions = schema.pop("$defs", {})
    return {**definitions, EVENT_SCHEMA_NAME: schema}
