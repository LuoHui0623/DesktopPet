-- 对话角色统一（Task.md T-053，A-32）：user 改为 master，assistant 改为 me。
-- event 与 system 保持不变。角色取值受 CHECK 约束，SQLite 无法直接修改，因此重建表。
-- proactive_feedback 外键引用 daily_log，重建时会级联清空该表；该表尚无写入代码，当前为空。

CREATE TABLE daily_log_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('master', 'me', 'system', 'event')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO daily_log_new (id, date, role, content, created_at)
SELECT
    id,
    date,
    CASE role WHEN 'user' THEN 'master' WHEN 'assistant' THEN 'me' ELSE role END,
    content,
    created_at
FROM daily_log;

DROP TABLE daily_log;

ALTER TABLE daily_log_new RENAME TO daily_log;

CREATE INDEX idx_daily_log_date ON daily_log (date);
