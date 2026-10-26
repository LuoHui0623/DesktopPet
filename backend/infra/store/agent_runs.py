from pathlib import Path

from infra.store.db import connect, transaction
from infra.store.repositories import agent_run
from infra.store.repositories.agent_run import AgentRunRecord


class DbAgentRuns:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def insert(self, record: AgentRunRecord) -> None:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                agent_run.insert(conn, record)
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
                agent_run.finish(
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
            return agent_run.get(conn, run_id)
        finally:
            conn.close()
