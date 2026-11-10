-- 结构化记忆条目：一条一个值，tags 为 JSON 数组；停用只改状态、不物理删除。
CREATE TABLE memory_structured (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    tags TEXT NOT NULL CHECK (json_valid(tags) AND json_type(tags) = 'array'),
    priority INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
    source_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status_state TEXT NOT NULL DEFAULT 'enabled'
        CHECK (status_state IN ('enabled', 'disabled')),
    status_reason TEXT
);

CREATE INDEX idx_memory_structured_state ON memory_structured (status_state);

-- 存量转换按 (source_ref, content) 判重；运行时条目 source_ref 为空，不受唯一约束。
CREATE UNIQUE INDEX idx_memory_structured_source_content
    ON memory_structured (source_ref, content)
    WHERE source_ref IS NOT NULL;
