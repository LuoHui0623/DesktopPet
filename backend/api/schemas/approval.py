from pydantic import BaseModel, ConfigDict, Field


class ApprovalItem(BaseModel):
    approval_id: str
    run_id: str
    capability: str
    task: str
    content: str
    tools: list[str] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    approved: bool


class ApprovalAck(BaseModel):
    approval_id: str
    approved: bool
