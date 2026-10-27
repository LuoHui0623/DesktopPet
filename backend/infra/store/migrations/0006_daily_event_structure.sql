-- 每日对话的 event 结构化（Task.md T-054，A-35）：会话边界改为 JSON 结构。
-- 旧版本的会话边界文本为“切换人设为 X，新会话开始”，转换为 {"type": "session_boundary", "persona": "X"}。
-- 无法识别的旧 event 保持原文，应用层不将其放入上下文，但仍保留在记忆中。

UPDATE daily_log
SET content = json_object(
    'type', 'session_boundary',
    'persona', trim(substr(content, 6, length(content) - 11))
)
WHERE role = 'event' AND content LIKE '切换人设为%，新会话开始';
