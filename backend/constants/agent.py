from constants.files import ACTION_ORDER

SUBAGENT_MAX_STEPS = 8
SUBAGENT_PURPOSE = "subagent"
DEFAULT_PERSONA_ID = "芙芙"
PERSONA_ACTIVE_KEY = "persona.active"
PHASE_DRAFT = "draft"
PHASE_EXECUTE = "execute"
PHASE_PLAN = "plan"
DRAFT_INVALID_LIMIT = 3  # 草案解析失败重试两次
PLAN_INVALID_LIMIT = 3  # 任务清单解析失败重试两次
# 任务规划者把一次派发整理成可并行执行的任务清单；项数上限与主代理派发独立。
TASK_PLAN_MIN_ITEMS = 1
TASK_PLAN_MAX_ITEMS = 5
READ_ONLY_TOOLS = ("index", "read", "grep")
# 文件域动作即工具；动作名与工具名一一对应，没有聚合的 edit 工具。
FILE_TOOLS = ACTION_ORDER
INDEX_TOOL = "index"
