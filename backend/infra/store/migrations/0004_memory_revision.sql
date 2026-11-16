-- Q27/T-12：条目增加单调递增 revision，用于设置页过期编辑的乐观冲突检测。
-- 既有条目初始化为 1，新条目从 1 开始；只增列，不改其余字段与行数，不建版本/审计表。
ALTER TABLE memory_structured ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;