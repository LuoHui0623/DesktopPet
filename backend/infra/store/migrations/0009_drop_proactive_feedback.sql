-- 主动消息反馈能力取消（Task.md T-067，D-46）：气泡忽略只做前端本地处理，后端没有反馈接口，
-- 该表也从未有写入方。表结构一并移除；历史数据始终为空，不需要搬迁。

DROP TABLE IF EXISTS proactive_feedback;
