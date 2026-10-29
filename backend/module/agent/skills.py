from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from constants.path import SKILLS_DIR
from module.agent.prompt import CapabilityDescription

HEAD_NOTE_PREFIX = ">"


class SkillError(Exception):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillSpec:
    """代码侧登记的技能骨架：能力名、说明书文件名、可用工具。"""

    name: str
    file: str
    tools: tuple[str, ...] = ()


class SkillRegistry:
    def __init__(self, directory: Path = SKILLS_DIR) -> None:
        self._directory = directory
        self._skills: dict[str, Skill] = {}
        self._files: dict[str, Path] = {}

    def register(self, spec: SkillSpec) -> None:
        if not spec.name:
            raise ValueError("能力名不能为空")
        if spec.name in self._skills:
            raise ValueError(f"能力 {spec.name} 已注册")
        path = self._directory / f"{spec.file}.md"
        self._skills[spec.name] = Skill(spec.name, parse_head_note(path), spec.tools)
        self._files[spec.name] = path

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._skills)

    def index(self) -> tuple[CapabilityDescription, ...]:
        return tuple(
            CapabilityDescription(name=skill.name, description=skill.description)
            for skill in self._skills.values()
        )

    def read(self, name: str) -> str:
        path = self._files.get(name)
        if path is None:
            raise SkillError(f"没有名为 {name} 的能力，请先用 index 查看能力清单")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillError(f"能力 {name} 的说明书无法读取：{exc}") from exc

    def summaries(self) -> Sequence[CapabilityDescription]:
        """主 agent 只见能力名，说明书由子代理自行查阅。"""
        return [
            CapabilityDescription(name=skill.name, description="")
            for skill in self._skills.values()
        ]


def parse_head_note(path: Path) -> str:
    """取说明书开头以 > 起头的连续行作为技能说明，正文由子代理查阅。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"技能说明书 {path.name} 无法读取：{exc}") from exc
    parts: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith(HEAD_NOTE_PREFIX):
            if parts:
                break
            continue
        parts.append(line[len(HEAD_NOTE_PREFIX) :].strip())
    note = "".join(parts).strip()
    if not note:
        raise SkillError(f"技能说明书 {path.name} 缺少以 {HEAD_NOTE_PREFIX} 开头的说明行")
    return note


MEMORY_WRITE_CAPABILITY = "记忆写入"
MEMORY_RETRIEVE_CAPABILITY = "记忆检索"
MEMORY_WRITE_TOOLS = ("index", "read", "memory_append", "memory_replace")
MEMORY_RETRIEVE_TOOLS = ("index", "read")


def memory_write_skill() -> SkillSpec:
    return SkillSpec(MEMORY_WRITE_CAPABILITY, "memory_write", MEMORY_WRITE_TOOLS)


def memory_retrieve_skill() -> SkillSpec:
    return SkillSpec(MEMORY_RETRIEVE_CAPABILITY, "memory_retrieve", MEMORY_RETRIEVE_TOOLS)
