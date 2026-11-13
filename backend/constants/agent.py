from constants.files import ACTION_ORDER

SUBAGENT_MAX_STEPS = 8
SUBAGENT_PURPOSE = "subagent"
DEFAULT_PERSONA_ID = "芙芙"
PERSONA_ACTIVE_KEY = "persona.active"
PHASE_DRAFT = "draft"
PHASE_EXECUTE = "execute"
DRAFT_INVALID_LIMIT = 3  # 草案解析失败重试两次
READ_ONLY_TOOLS = ("index", "read", "grep")
# 文件域动作即工具；动作名与工具名一一对应，没有聚合的 edit 工具。
FILE_TOOLS = ACTION_ORDER
INDEX_TOOL = "index"
