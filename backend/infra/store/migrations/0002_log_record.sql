-- 日志入库。由 infra/logging.py 的 DbLogHandler 批量写入。
-- created_at 为带时区偏移、精确到毫秒的 ISO 8601 文本。

CREATE TABLE log_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    logger TEXT NOT NULL,
    message TEXT NOT NULL,
    exc_text TEXT
);
CREATE INDEX idx_log_record_time ON log_record (created_at);
CREATE INDEX idx_log_record_level ON log_record (level, created_at);
