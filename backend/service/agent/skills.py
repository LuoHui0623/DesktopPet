from collections.abc import Sequence
from dataclasses import dataclass

from service.agent.prompt import CapabilityDescription


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    keywords: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._keyword_owner: dict[str, str] = {}

    def register(self, skill: Skill) -> None:
        if not skill.name:
            raise ValueError("能力名不能为空")
        if skill.name in self._skills:
            raise ValueError(f"能力 {skill.name} 已注册")
        for keyword in skill.keywords:
            if not keyword:
                raise ValueError(f"能力 {skill.name} 的关键词不能为空")
            owner = self._keyword_owner.get(keyword)
            if owner is not None:
                raise ValueError(f"关键词 {keyword!r} 已绑定能力 {owner}")
        self._skills[skill.name] = skill
        for keyword in skill.keywords:
            self._keyword_owner[keyword] = skill.name

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def match(self, text: str) -> list[Skill]:
        lowered = text.lower()
        return [
            skill
            for skill in self._skills.values()
            if any(keyword.lower() in lowered for keyword in skill.keywords)
        ]

    def summaries(self) -> Sequence[CapabilityDescription]:
        return [
            CapabilityDescription(name=skill.name, description=skill.description)
            for skill in self._skills.values()
        ]


MEMORY_WRITE_CAPABILITY = "记忆写入"
MEMORY_WRITE_KEYWORDS = ("记住", "记一下", "我喜欢")


def memory_write_skill() -> Skill:
    return Skill(
        name=MEMORY_WRITE_CAPABILITY,
        description=(
            "判断用户这句话是否包含值得长期记住的信息。"
            "若是新信息，用 memory_create 新增一条记忆。"
            "若与已有记忆是同一主题的新说法，先用 memory_index 与 memory_read 找到该条记忆的 id，"
            "再用 memory_update 覆盖其内容。"
            "不要记录用户没有表达的内容。完成后用一句话告知用户记住了什么。"
        ),
        keywords=MEMORY_WRITE_KEYWORDS,
        tools=("memory_index", "memory_read", "memory_create", "memory_update"),
    )
