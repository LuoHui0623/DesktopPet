import sqlite3
from dataclasses import dataclass

COLUMNS = (
    "id, session_id, capability, instruction, steps, output, status, error_code, "
    "started_at, finished_at"
)


@dataclass(frozen=True)
class AgentRunRecord:
    id: str
    session_id: str
    capability: str
    instruction: str
    steps: str
    output: str | None
    status: str
    error_code: str | None
    started_at: str
    finished_at: str | None


def insert(conn: sqlite3.Connection, record: AgentRunRecord) -> None:
    conn.execute(
        f"INSERT INTO agent_run ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.id,
            record.session_id,
            record.capability,
            record.instruction,
            record.steps,
            record.output,
            record.status,
            record.error_code,
            record.started_at,
            record.finished_at,
        ),
    )


def finish(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    output: str | None,
    error_code: str | None,
    steps: str,
    finished_at: str,
) -> None:
    conn.execute(
        "UPDATE agent_run SET status = ?, output = ?, error_code = ?, steps = ?, "
        "finished_at = ? WHERE id = ?",
        (status, output, error_code, steps, finished_at, run_id),
    )


def get(conn: sqlite3.Connection, run_id: str) -> AgentRunRecord | None:
    row = conn.execute(f"SELECT {COLUMNS} FROM agent_run WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return AgentRunRecord(*tuple(row))
