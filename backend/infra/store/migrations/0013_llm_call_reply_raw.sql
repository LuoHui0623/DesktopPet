-- 模型未解析的原始响应：正文原样保存，结构化内容序列化为 JSON，供状态窗口排查非标准输出。
-- 与 reply_text（归一化后的纯文本）并存，失败调用留 NULL。

ALTER TABLE llm_call ADD COLUMN reply_raw TEXT;
