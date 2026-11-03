"""Chat 系统提示词的主题契约与注册表。

每个主题声明 id、category 与 render(ctx)；render 只返回正文，不负责
`【主题】` 包装，也不负责范畴分隔线——这两件事统一由装配器完成。

`TOPIC_REGISTRY` 记录注册顺序；输出时先按范畴顺序分组，再按注册顺序排列
组内主题。范畴顺序固定为 身份 → 能力 → 行为。
"""

from collections.abc import Callable
from dataclasses import dataclass
from string import Template
from typing import Literal

from module.agent.chat_output import ACTION_RULES_BODY, render_action_rules

# 上下文与系统提示词里的分隔线统一居中到该宽度（含标签）。
SEPARATOR_WIDTH = 30

CATEGORY_IDENTITY = "身份"
CATEGORY_CAPABILITY = "能力"
CATEGORY_BEHAVIOR = "行为"

Category = Literal["身份", "能力", "行为"]
CATEGORY_ORDER: tuple[Category, ...] = (
    CATEGORY_IDENTITY,
    CATEGORY_CAPABILITY,
    CATEGORY_BEHAVIOR,
)


@dataclass(frozen=True)
class TopicContext:
    """渲染主题正文所需的全部占位值。"""

    persona_section: str = ""
    capabilities: str = ""
    resources: str = ""
    sentence_max_chars: str = ""
    sentence_max_width: str = ""
    persona_hint_chars: str = ""

    def as_mapping(self) -> dict[str, str]:
        return {
            "persona_section": self.persona_section,
            "capabilities": self.capabilities,
            "resources": self.resources,
            "sentence_max_chars": self.sentence_max_chars,
            "sentence_max_width": self.sentence_max_width,
            "persona_hint_chars": self.persona_hint_chars,
        }


Render = Callable[[TopicContext], str]


@dataclass(frozen=True)
class Topic:
    id: str
    category: Category
    render: Render


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

CAPABILITIES_BODY = """下面是你可以交办的事项；派发时按任务内容描述，不必指定名称。
$capabilities"""

RESOURCES_BODY = """$resources"""

SPEECH_RULES_BODY = """- 下面的长度上限只管 text 数组里解析出的每一条发言（每一个 str），不管整个 output 有多长：四条发言加起来多长都不算超限。
- 每条发言不超过 $sentence_max_chars 个汉字或 $sentence_max_width 个英文字符（按显示宽度算，一个汉字占 2）。
- 一条发言里出现句号、问号、叹号、省略号时按句拆开，拆出来的每一句都要单独满足上面的上限。
- 人设提示约 $persona_hint_chars 字。"""

CONSTRAINTS_BODY = """每次说话要用 `{}` JSON 格式，"type" 必须是第一个字段。"""

TOPIC_BODIES: dict[str, str] = {
    "人设": PERSONA_BODY,
    "可用能力": CAPABILITIES_BODY,
    "可用资源": RESOURCES_BODY,
    "行动规则": ACTION_RULES_BODY,
    "发言规则": SPEECH_RULES_BODY,
    "约束": CONSTRAINTS_BODY,
}


def _template_render(body: str) -> Render:
    """把主题正文的占位符替换成上下文值；替换只做一遍，不递归解释值里的 `$`。"""
    template = Template(body)

    def render(context: TopicContext) -> str:
        try:
            return template.substitute(context.as_mapping())
        except KeyError as exc:
            raise KeyError(f"主题正文缺少占位符 {exc}") from exc
        except ValueError as exc:
            raise ValueError(f"主题正文占位符不合法：{exc}") from exc

    return render


TOPIC_PERSONA = Topic("人设", CATEGORY_IDENTITY, _template_render(PERSONA_BODY))
TOPIC_CAPABILITIES = Topic("可用能力", CATEGORY_CAPABILITY, _template_render(CAPABILITIES_BODY))
TOPIC_RESOURCES = Topic("可用资源", CATEGORY_CAPABILITY, _template_render(RESOURCES_BODY))
TOPIC_ACTION_RULES = Topic("行动规则", CATEGORY_BEHAVIOR, render_action_rules)
TOPIC_SPEECH_RULES = Topic("发言规则", CATEGORY_BEHAVIOR, _template_render(SPEECH_RULES_BODY))
TOPIC_CONSTRAINTS = Topic("约束", CATEGORY_BEHAVIOR, _template_render(CONSTRAINTS_BODY))

# 注册顺序只在这里声明；输出时范畴顺序优先，范畴内沿用这里的顺序。
TOPIC_REGISTRY: tuple[Topic, ...] = (
    TOPIC_PERSONA,
    TOPIC_CAPABILITIES,
    TOPIC_RESOURCES,
    TOPIC_ACTION_RULES,
    TOPIC_SPEECH_RULES,
    TOPIC_CONSTRAINTS,
)
