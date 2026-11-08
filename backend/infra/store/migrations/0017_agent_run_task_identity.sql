-- agent_run 记录派发时的人设与任务身份（Task1.md 第六节第 10 条，P0）。
-- 进行中任务沿用派发时的 persona_id/persona_version，不随当前启用人设变化。

ALTER TABLE agent_run ADD COLUMN persona_id TEXT;
ALTER TABLE agent_run ADD COLUMN persona_version INTEGER;
ALTER TABLE agent_run ADD COLUMN main_task_id TEXT;
ALTER TABLE agent_run ADD COLUMN source TEXT;

CREATE INDEX idx_agent_run_main_task ON agent_run (main_task_id);
