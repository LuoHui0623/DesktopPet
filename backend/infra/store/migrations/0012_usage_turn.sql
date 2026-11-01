-- 模型调用与子代理执行按对话轮次关联（Task.md T-04 / Q-07、D-09）。
-- 历史记录没有轮次归属，保留 NULL；轮次由 ChatService 生成并显式传入线程池。

ALTER TABLE llm_call ADD COLUMN turn_id TEXT;
CREATE INDEX idx_llm_call_turn ON llm_call (turn_id);

ALTER TABLE agent_run ADD COLUMN turn_id TEXT;
CREATE INDEX idx_agent_run_turn ON agent_run (turn_id);
