-- Q26/T-11：结构化记忆改为全量注入、必保且不排序，priority 没有消费方，连同历史评级值一并移除。
-- 只删列：保留每条记录的 id、content、tags、source_ref、时间戳与状态字段，行数与其余字段值不变。
ALTER TABLE memory_structured DROP COLUMN priority;