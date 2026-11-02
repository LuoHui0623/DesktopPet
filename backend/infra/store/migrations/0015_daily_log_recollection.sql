-- daily_log 允许「回忆」角色：每个「人设 + 日期」只保留一条当日回忆（Task.md T-07）。
-- 角色取值受 CHECK 约束，SQLite 无法直接修改，因此重建表。
-- 回忆属于当天的旁注，不进聊天时间线，由服务层的 CHAT_ROLES 过滤保证。
-- 同一人设同一天的回忆唯一性用表达式部分唯一索引兜底：
-- 索引只覆盖 role = '回忆' 的行，其它角色的重复写入不受影响。

CREATE TABLE daily_log_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('master', 'me', 'assistant', 'system', 'event', '回忆')),
    content TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO daily_log_new (id, event_time, role, content, persona_id, created_at)
SELECT id, event_time, role, content, persona_id, created_at FROM daily_log;

DROP TABLE daily_log;

ALTER TABLE daily_log_new RENAME TO daily_log;

CREATE INDEX idx_daily_log_event_time ON daily_log (event_time);
CREATE INDEX idx_daily_log_persona_event_time ON daily_log (persona_id, event_time);
CREATE UNIQUE INDEX idx_daily_log_recollection
    ON daily_log (persona_id, substr(event_time, 1, 10))
    WHERE role = '回忆';
