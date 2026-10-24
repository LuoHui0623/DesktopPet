-- 初始表结构，对应 PRD §7.7（llm_provider 不入库，模型配置来自 config/llm.yaml）。
-- 时间字段为带时区偏移的 ISO 8601 文本；日期字段为 YYYY-MM-DD。

CREATE TABLE daily_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'event')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_daily_log_date ON daily_log (date);

CREATE TABLE daily_summary (
    date TEXT PRIMARY KEY,
    summary TEXT NOT NULL
);

CREATE TABLE memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK (category IN ('profile', 'preference', 'habit', 'event', 'interaction')),
    content TEXT NOT NULL,
    explicit INTEGER NOT NULL CHECK (explicit IN (0, 1)),
    importance INTEGER NOT NULL DEFAULT 1 CHECK (importance BETWEEN 1 AND 5),
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    source_log_ids TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_memory_valid ON memory (valid_from, valid_to);

CREATE TABLE memory_archive (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    explicit INTEGER NOT NULL,
    importance INTEGER NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    source_log_ids TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT NOT NULL
);

CREATE TABLE core_memory (
    memory_id INTEGER PRIMARY KEY REFERENCES memory (id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL
);

CREATE TABLE consolidation_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    added INTEGER NOT NULL DEFAULT 0,
    expired INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed'))
);

CREATE TABLE llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    latency_ms INTEGER,
    success INTEGER NOT NULL DEFAULT 1 CHECK (success IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE persona_version (
    version INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE behavior_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);
CREATE INDEX idx_behavior_event_time ON behavior_event (occurred_at);

CREATE TABLE proactive_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER REFERENCES daily_log (id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('like', 'dislike', 'ignore')),
    created_at TEXT NOT NULL
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

CREATE TABLE weather_cache (
    city_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE app_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
