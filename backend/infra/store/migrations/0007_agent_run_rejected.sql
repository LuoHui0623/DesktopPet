-- agent_run.status 增加 rejected（Task.md T-049、T-050，A-29）：草案被用户拒绝的项记为 rejected。
-- SQLite 不能直接修改 CHECK 约束，按官方做法重建表并搬回数据。

CREATE TABLE agent_run_next (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    instruction TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '[]',
    output TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'rejected')),
    error_code TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

INSERT INTO agent_run_next (
    id, session_id, capability, instruction, steps, output, status, error_code, started_at, finished_at
)
SELECT id, session_id, capability, instruction, steps, output, status, error_code, started_at, finished_at
FROM agent_run;

DROP TABLE agent_run;
ALTER TABLE agent_run_next RENAME TO agent_run;
CREATE INDEX idx_agent_run_session ON agent_run (session_id, started_at);
