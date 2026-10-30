from dataclasses import dataclass


@dataclass(frozen=True)
class UsageRecord:
    provider: str
    model: str
    purpose: str | None
    run_id: str | None
    tokens_in: int | None
    tokens_out: int | None
    usage_raw: str | None
    started_at: str
    finished_at: str
    latency_ms: int
    success: bool
    prompt_hash: str | None = None
    prompt_text: str | None = None
    reply_text: str | None = None
