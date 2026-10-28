-- 结构化记忆改为单个纯文本大纲文档（data/memory/structured.md），不再有 id。
-- core_memory 不再由 Pet 管理：服务预置、用户自行写入，表格与相关 API 一并移除。

DROP TABLE IF EXISTS core_memory;
