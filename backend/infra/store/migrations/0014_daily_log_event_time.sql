-- daily_log 的时间列改为事件发生时刻（带时区偏移的 ISO 8601 文本），不再只存日期。
-- 旧的 date 只有日期，用同行 created_at 回填精确时刻；created_at 保留为入库时间。

ALTER TABLE daily_log RENAME COLUMN date TO event_time;

UPDATE daily_log SET event_time = created_at;

DROP INDEX idx_daily_log_date;
DROP INDEX idx_daily_log_persona_date;

CREATE INDEX idx_daily_log_event_time ON daily_log (event_time);
CREATE INDEX idx_daily_log_persona_event_time ON daily_log (persona_id, event_time);
