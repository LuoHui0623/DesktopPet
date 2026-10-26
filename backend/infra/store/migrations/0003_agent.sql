-- llm_usage 扩展、agent_run 表、旧表清理（Task.md T-020，Q-3 默认方案）。
-- 结构化记忆存放于 data/memory/structured/<id>.json，memory 表删除。
-- core_memory 改为引用结构化记忆的 id（TEXT）；旧表中的典藏引用无法对应到新 id，不迁移。

ALTER TABLE llm_usage ADD COLUMN purpose TEXT;
ALTER TABLE llm_usage ADD COLUMN run_id TEXT;
ALTER TABLE llm_usage ADD COLUMN started_at TEXT;
ALTER TABLE llm_usage ADD COLUMN finished_at TEXT;
ALTER TABLE llm_usage ADD COLUMN usage_raw TEXT;
ALTER TABLE llm_usage ADD COLUMN prompt_hash TEXT;

CREATE TABLE agent_run (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    instruction TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '[]',
    output TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    error_code TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX idx_agent_run_session ON agent_run (session_id, started_at);

DROP TABLE memory_archive;
DROP TABLE daily_summary;
DROP TABLE consolidation_run;

DROP TABLE core_memory;
CREATE TABLE core_memory (
    memory_id TEXT PRIMARY KEY,
    sort_order INTEGER NOT NULL
);

DROP TABLE memory;
