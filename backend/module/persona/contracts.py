from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaVersion:
    persona_id: str
    version: int
    content: str
    created_at: str
