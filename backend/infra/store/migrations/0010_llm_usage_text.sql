-- 每次模型调用连同请求与回复正文一起落库（Task.md D-15）。
-- 正文由 constants.llm.LLM_CALL_TEXT_LIMIT 截断后写入，便于复盘模型非标准输出。

ALTER TABLE llm_usage ADD COLUMN prompt_text TEXT;
ALTER TABLE llm_usage ADD COLUMN reply_text TEXT;
