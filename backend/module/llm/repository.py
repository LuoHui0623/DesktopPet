import sqlite3
from pathlib import Path

from infra.llm.usage import UsageSample, UsageSink
from infra.store.db import connect
from module.llm.contracts import UsageRecord


def to_record(sample: UsageSample) -> UsageRecord:
    return UsageRecord(
        provider=sample.provider,
        model=sample.model,
        purpose=sample.purpose,
        run_id=sample.run_id,
        tokens_in=sample.tokens_in,
        tokens_out=sample.tokens_out,
        usage_raw=sample.usage_raw,
        started_at=sample.started_at,
        finished_at=sample.finished_at,
        latency_ms=sample.latency_ms,
        success=sample.success,
        prompt_hash=sample.prompt_hash,
    )


def insert(conn: sqlite3.Connection, record: UsageRecord) -> None:
    conn.execute(
        "INSERT INTO llm_usage (provider, model, purpose, run_id, tokens_in, tokens_out, "
        "usage_raw, started_at, finished_at, latency_ms, success, prompt_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            record.prompt_hash,
            record.finished_at,
        ),
    )


def db_usage_sink(db_file: Path) -> UsageSink:
    def write(sample: UsageSample) -> None:
        conn = connect(db_file)
        try:
            insert(conn, to_record(sample))
        finally:
            conn.close()

    return write
