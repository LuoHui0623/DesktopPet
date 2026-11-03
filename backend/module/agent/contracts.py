from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRunRecord:
    id: str
    session_id: str
    skill: str
    instruction: str
    steps: str
    output: str | None
    status: str
    error_code: str | None
    started_at: str
    finished_at: str | None
    turn_id: str | None = None
