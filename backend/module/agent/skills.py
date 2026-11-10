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
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillSpec:
    """代码侧登记的技能骨架：技能名、说明书文件名、可用工具与路由关键词。"""

    name: str
    file: str
    tools: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


class SkillRegistry:
    def __init__(self, directory: Path = SKILLS_DIR) -> None:
        self._directory = directory
        self._skills: dict[str, Skill] = {}
        self._files: dict[str, Path] = {}

    def register(self, spec: SkillSpec) -> None:
        if not spec.name:
            raise ValueError("技能名不能为空")
        if spec.name in self._skills:
            raise ValueError(f"技能 {spec.name} 已注册")
        path = self._directory / f"{spec.file}.md"
        self._skills[spec.name] = Skill(spec.name, parse_head_note(path), spec.tools, spec.keywords)
        self._files[spec.name] = path

    def route(self, task: str) -> Skill | None:
        """按任务内容挑选技能；topic 不参与路由（D-23）。"""
        text = task.strip()
        if not text:
            return None
        best: tuple[int, int, Skill] | None = None
        for order, skill in enumerate(self._skills.values()):
            score = sum(len(keyword) for keyword in skill.keywords if keyword and keyword in text)
            if score == 0:
                continue
            candidate = (score, -order, skill)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        if best is not None:
            return best[2]
        # 任务里直接点名技能时兜底；关键词优先，避免名字里常见的字压过意图。
        for skill in self._skills.values():
            if skill.name and skill.name in text:
                return skill
        return None

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


MEMORY_WRITE_SKILL = "记忆写入"
MEMORY_RETRIEVE_SKILL = "记忆检索"
DRAFT_NOTE_SKILL = "自记笔记"
MEMORY_WRITE_TOOLS = ("index", "read", "grep", "memory")
MEMORY_RETRIEVE_TOOLS = ("index", "read", "grep")
DRAFT_NOTE_TOOLS = ("index", "read", "grep", "edit")
MEMORY_WRITE_KEYWORDS = (
    "记住",
    "记下",
    "记录下来",
    "写入记忆",
    "保存记忆",
    "更新记忆",
    "存入记忆",
    "记到记忆",
)
MEMORY_RETRIEVE_KEYWORDS = (
    "回忆",
    "检索",
    "查找",
    "查一下",
    "查询",
    "搜索",
    "记得",
    "以前",
    "之前",
    "历史",
    "说过",
    "聊过",
    "偏好",
)
DRAFT_NOTE_KEYWORDS = (
    "记一下",
    "笔记",
    "便签",
    "备忘",
    "待办",
    "提醒",
    "清单",
    "日程",
)


def memory_write_skill() -> SkillSpec:
    return SkillSpec(MEMORY_WRITE_SKILL, "memory_write", MEMORY_WRITE_TOOLS, MEMORY_WRITE_KEYWORDS)


def memory_retrieve_skill() -> SkillSpec:
    return SkillSpec(
        MEMORY_RETRIEVE_SKILL, "memory_retrieve", MEMORY_RETRIEVE_TOOLS, MEMORY_RETRIEVE_KEYWORDS
    )


def draft_note_skill() -> SkillSpec:
    return SkillSpec(DRAFT_NOTE_SKILL, "draft_note", DRAFT_NOTE_TOOLS, DRAFT_NOTE_KEYWORDS)
