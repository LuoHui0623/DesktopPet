-- 人设卡片（Task.md T-046，D-13）：persona_version 按 persona_id 分别记录版本。
-- 已有记录均属于默认人设“芙芙”（constants/agent.py 的 DEFAULT_PERSONA_ID）。
-- 主键由 version 改为 (persona_id, version)，因此需要重建表。

CREATE TABLE persona_version_new (
    persona_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (persona_id, version)
);

INSERT INTO persona_version_new (persona_id, version, content, created_at)
SELECT '芙芙', version, content, created_at FROM persona_version;

DROP TABLE persona_version;

ALTER TABLE persona_version_new RENAME TO persona_version;
