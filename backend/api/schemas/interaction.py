from typing import Literal

from pydantic import BaseModel, ConfigDict

InteractionKind = Literal["click", "hover", "drag", "menu"]


class InteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: InteractionKind


class InteractionResponse(BaseModel):
    accepted: bool
    reason: str | None
