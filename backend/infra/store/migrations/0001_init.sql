-- 基线：由 0001–0017 压缩生成，此后只增不改。
-- 生成方式：scripts/squash_migrations.py（不含 schema_version）。
CREATE TABLE "agent_run" (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    instruction TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '[]',
    output TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'rejected')),
    error_code TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
, turn_id TEXT, persona_id TEXT, persona_version INTEGER, main_task_id TEXT, source TEXT);
CREATE TABLE app_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE behavior_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);
CREATE TABLE capability_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    params_summary TEXT NOT NULL,
    risk TEXT NOT NULL CHECK (risk IN ('L0', 'L1', 'L2')),
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
    result TEXT,
    undo_payload TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE chat_import (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    conversation TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    file_path TEXT
);
CREATE TABLE "daily_log" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('master', 'me', 'assistant', 'system', 'event', '回忆')),
    content TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE "llm_call" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    latency_ms INTEGER,
    success INTEGER NOT NULL DEFAULT 1 CHECK (success IN (0, 1)),
    created_at TEXT NOT NULL
, purpose TEXT, run_id TEXT, started_at TEXT, finished_at TEXT, usage_raw TEXT, prompt_hash TEXT, prompt_text TEXT, reply_text TEXT, turn_id TEXT, reply_raw TEXT);
CREATE TABLE log_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    logger TEXT NOT NULL,
    message TEXT NOT NULL,
    exc_text TEXT
);
CREATE TABLE "persona_version" (
    persona_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (persona_id, version)
);
CREATE TABLE weather_cache (
    city_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX idx_agent_run_main_task ON agent_run (main_task_id);
CREATE INDEX idx_agent_run_session ON agent_run (session_id, started_at);
CREATE INDEX idx_agent_run_turn ON agent_run (turn_id);
CREATE INDEX idx_behavior_event_time ON behavior_event (occurred_at);
CREATE INDEX idx_daily_log_event_time ON daily_log (event_time);
CREATE INDEX idx_daily_log_persona_event_time ON daily_log (persona_id, event_time);
CREATE UNIQUE INDEX idx_daily_log_recollection
    ON daily_log (persona_id, substr(event_time, 1, 10))
    WHERE role = '回忆';
CREATE INDEX idx_llm_call_turn ON llm_call (turn_id);
CREATE INDEX idx_log_record_level ON log_record (level, created_at);
CREATE INDEX idx_log_record_time ON log_record (created_at);
