from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


class SourceOut(BaseModel):
    id: str
    kind: str
    category: str | None = None
    date: str
    text: str


class ChatReplyResponse(BaseModel):
    message_id: str
    text: str
    recorded: bool
    sources: list[SourceOut] = Field(default_factory=list)


class ChatHistoryItem(BaseModel):
    id: int
    date: str
    role: str
    content: str
    created_at: str


class ChatHistoryResponse(BaseModel):
    items: list[ChatHistoryItem]
    has_more: bool
