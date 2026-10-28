from collections.abc import Sequence
from dataclasses import dataclass

from service.agent.prompt import CapabilityDescription


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    tools: tuple[str, ...] = ()


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if not skill.name:
            raise ValueError("能力名不能为空")
        if skill.name in self._skills:
            raise ValueError(f"能力 {skill.name} 已注册")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def summaries(self) -> Sequence[CapabilityDescription]:
        return [
            CapabilityDescription(name=skill.name, description=skill.description)
            for skill in self._skills.values()
        ]


MEMORY_WRITE_CAPABILITY = "记忆写入"


def memory_write_skill() -> Skill:
    return Skill(
        name=MEMORY_WRITE_CAPABILITY,
        description=(
            "把主人值得长期记住的信息写进记忆库。"
            "主人明确提出「记住」「记一下」，说出会持续成立的偏好、习惯、约定或重要安排，"
            "或纠正了以前记下的同类信息时，都用这个能力。"
            "一次性、临时的信息不写入。"
            "新信息用 memory_create 新增一条记忆；"
            "与已有记忆是同一主题的新说法，先用 memory_index 与 memory_read 找到那条记忆的 id，"
            "再用 memory_update 覆盖其内容。"
            "不要记录主人没有表达的内容。完成后用一句话告知主人记住了什么。"
        ),
        tools=("memory_index", "memory_read", "memory_create", "memory_update"),
    )
