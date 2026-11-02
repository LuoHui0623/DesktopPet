"""Chat 系统提示词的主题正文与拼接顺序。

本模块只声明两样数据：

1. 每个主题的正文，原样写在三引号字符串里，不做任何行拼接；
2. `TOPIC_ORDER`——唯一决定主题顺序的有序列表。

把主题包成 `【<主题>】\n<正文>` 再按 `TOPIC_ORDER` 串联成一份提示词，属于拼装动作，
由 chat service 在每轮构建系统提示词时完成（渲染入口放在 `module.agent.prompt`），本模块不参与。
"""

# 上下文与系统提示词里的分隔线统一居中到该宽度（含标签）。
SEPARATOR_WIDTH = 30

TOPIC_PERSONA = "人设"
TOPIC_ACTION_RULES = "行动规则"
TOPIC_CAPABILITIES = "可用能力"
TOPIC_RESOURCES = "可用资源"
TOPIC_SPEECH_RULES = "发言规则"
TOPIC_CONSTRAINTS = "约束"

# 系统提示词的主题顺序只由这个列表决定，调用方无法绕过它另行排序。
TOPIC_ORDER: tuple[str, ...] = (
    TOPIC_PERSONA,
    TOPIC_ACTION_RULES,
    TOPIC_CAPABILITIES,
    TOPIC_RESOURCES,
    TOPIC_SPEECH_RULES,
    TOPIC_CONSTRAINTS,
)

# 全部正文合起来必须正好用到这些 $占位符。
CHAT_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "persona_section",
        "capabilities",
        "resources",
        "sentence_max_chars",
        "sentence_max_width",
        "persona_hint_chars",
    }
)

PERSONA_BODY = """$persona_section"""

ACTION_RULES_BODY = """每次说话要用 `{}` JSON 格式!!!
每次对话前，你可以先派发一次任务，最多一次哦

一、对话
这是对话格式：`{"type": "chat", "text": ["要说给主人的话"]}`
- "type" 为 "chat" 时表示你要说话
- "text" 要求是 `Sequence[str]`，每个 str 是你想说的一句话，最多说四句话哦！

二、派发任务
这是派发任务格式：`{"type": "dispatch", "plan": [{"capability": "能力名", "task": "任务描述"}]}`
- "type" 为 "dispatch" 时表示你想派发任务
- "plan" 要求是 `list[dict[capability, task]]`，包含 1–3 项，每项恰好有 capability 和 task 两个字段，都不能为空。
- task 是给执行者的独立任务描述，写清要做什么、范围与限制，不要写成对话。
- 只有当主人明确需要执行能力时才派发；一次派发不要超过 3 项，也不要拆成多次回复派发。
- 派发后由系统执行并回报结果，当前回复不要再补充执行细节。"""

CAPABILITIES_BODY = """只提供能力名，具体做法由执行者自行查阅说明书。
$capabilities"""

RESOURCES_BODY = """$resources"""

SPEECH_RULES_BODY = """- 下面的长度上限只管 text 数组里解析出的每一条发言（每一个 str），不管整个 output 有多长：四条发言加起来多长都不算超限。
- 每条发言不超过 $sentence_max_chars 个汉字或 $sentence_max_width 个英文字符（按显示宽度算，一个汉字占 2）。
- 一条发言里出现句号、问号、叹号、省略号时按句拆开，拆出来的每一句都要单独满足上面的上限。
- 人设提示约 $persona_hint_chars 字。"""

CONSTRAINTS_BODY = """每次说话要用 `{}` JSON 格式，"type" 必须是第一个字段。"""

TOPIC_BODIES: dict[str, str] = {
    TOPIC_PERSONA: PERSONA_BODY,
    TOPIC_ACTION_RULES: ACTION_RULES_BODY,
    TOPIC_CAPABILITIES: CAPABILITIES_BODY,
    TOPIC_RESOURCES: RESOURCES_BODY,
    TOPIC_SPEECH_RULES: SPEECH_RULES_BODY,
    TOPIC_CONSTRAINTS: CONSTRAINTS_BODY,
}
