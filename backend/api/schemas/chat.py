from datetime import datetime

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


class ReplyMessageOut(BaseModel):
    """一轮回复里的一条发言。"""

    message_id: str
    text: str
    at: datetime
    sources: list[SourceOut] = Field(default_factory=list)


class ChatReplyResponse(BaseModel):
    message_id: str
    text: str
    recorded: bool
    sources: list[SourceOut] = Field(default_factory=list)
    messages: list[ReplyMessageOut] = Field(default_factory=list)


class ChatHistoryResponse(BaseModel):
    items: list[DailyLogView]
    has_more: bool
