-- 模型调用记录：表名与能力命名对齐，并补请求与回复正文。
-- 正文由 constants.llm.LLM_CALL_TEXT_LIMIT 截断后写入，便于复盘模型非标准输出。

ALTER TABLE llm_usage RENAME TO llm_call;
ALTER TABLE llm_call ADD COLUMN prompt_text TEXT;
ALTER TABLE llm_call ADD COLUMN reply_text TEXT;
