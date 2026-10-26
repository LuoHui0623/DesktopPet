import sqlite3
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


def insert(conn: sqlite3.Connection, record: UsageRecord) -> None:
    conn.execute(
        "INSERT INTO llm_usage (provider, model, purpose, run_id, tokens_in, tokens_out, "
        "usage_raw, started_at, finished_at, latency_ms, success, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.provider,
            record.model,
            record.purpose,
            record.run_id,
            record.tokens_in,
            record.tokens_out,
            record.usage_raw,
            record.started_at,
            record.finished_at,
            record.latency_ms,
            int(record.success),
            record.finished_at,
        ),
    )
