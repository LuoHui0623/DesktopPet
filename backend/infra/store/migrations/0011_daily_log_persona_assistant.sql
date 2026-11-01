-- 聊天记录补上人设归属，并允许子代理回灌使用 assistant 角色（Task.md T-01 / D-23、D-24）。
-- 已有记录按当前启用的人设回填；没有设置时使用默认人设「芙芙」。
-- 角色取值受 CHECK 约束，SQLite 无法直接修改，因此重建表。

CREATE TABLE daily_log_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('master', 'me', 'assistant', 'system', 'event')),
    content TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO daily_log_new (id, date, role, content, persona_id, created_at)
SELECT
    id,
    date,
    role,
    content,
    COALESCE((SELECT value FROM app_setting WHERE key = 'persona.active'), '芙芙'),
    created_at
FROM daily_log;

DROP TABLE daily_log;

ALTER TABLE daily_log_new RENAME TO daily_log;

CREATE INDEX idx_daily_log_date ON daily_log (date);
CREATE INDEX idx_daily_log_persona_date ON daily_log (persona_id, date);
