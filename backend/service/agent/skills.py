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
