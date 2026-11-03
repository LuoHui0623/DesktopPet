-- agent_run.capability 改为 skill（Task.md T-07、D-29）：
-- 该列只作审计，存按任务内容推断出的技能名，未命中时存 natural，不再是模型可见的派发字段。

ALTER TABLE agent_run RENAME COLUMN capability TO skill;