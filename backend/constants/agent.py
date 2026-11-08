SUBAGENT_MAX_STEPS = 8
SUBAGENT_PURPOSE = "subagent"
DEFAULT_PERSONA_ID = "芙芙"
PERSONA_ACTIVE_KEY = "persona.active"
PHASE_DRAFT = "draft"
PHASE_EXECUTE = "execute"
DRAFT_INVALID_LIMIT = 3  # 草案解析失败重试两次
READ_ONLY_TOOLS = ("index", "read", "grep")
# 使用这些工具的子代理任务必须先调用 index 发现能力与路径。
FILE_TOOLS = ("index", "read", "grep", "edit")
INDEX_TOOL = "index"
