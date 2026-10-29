from pydantic import BaseModel, ConfigDict, Field

from module.memory.contracts import DailyLogView


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


class SourceOut(BaseModel):
    id: str
    kind: str
    date: str
    text: str


class ChatReplyResponse(BaseModel):
    message_id: str
    text: str
    recorded: bool
    sources: list[SourceOut] = Field(default_factory=list)


class ChatHistoryResponse(BaseModel):
    items: list[DailyLogView]
    has_more: bool
