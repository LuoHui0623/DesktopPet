from pydantic import BaseModel, ConfigDict, Field


class PersonaItemOut(BaseModel):
    id: str
    name: str | None = None
    available: bool
    reason: str | None = None
    active: bool


class PersonasResponse(BaseModel):
    active: str
    personas: list[PersonaItemOut]


class PersonaActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=32)
