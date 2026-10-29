import sqlite3
from pathlib import Path

from infra.store.db import connect, transaction
from module.agent.contracts import AgentRunRecord

COLUMNS = (
    "id, session_id, capability, instruction, steps, output, status, error_code, "
    "started_at, finished_at"
)


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


class DbAgentRuns:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def insert(self, record: AgentRunRecord) -> None:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                insert(conn, record)
        finally:
            conn.close()

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        output: str | None,
        error_code: str | None,
        steps: str,
        finished_at: str,
    ) -> None:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                finish(
                    conn,
                    run_id,
                    status=status,
                    output=output,
                    error_code=error_code,
                    steps=steps,
                    finished_at=finished_at,
                )
        finally:
            conn.close()

    def get(self, run_id: str) -> AgentRunRecord | None:
        conn = connect(self._db_file)
        try:
            return get(conn, run_id)
        finally:
            conn.close()
