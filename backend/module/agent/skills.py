from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from constants.agent import READ_ONLY_TOOLS
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
    manual: str = ""
    planner: bool = False


@dataclass(frozen=True)
class SkillSpec:
    """代码侧登记的技能骨架：技能名、说明书文件名、可用工具与路由关键词。

    `planner` 标记内部任务规划能力：它由派发执行器固定使用，不参与关键词路由，
    也不出现在主代理或子代理的能力清单里。
    """

    name: str
    file: str
    tools: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    planner: bool = False


class SkillRegistry:
    def __init__(self, directory: Path = SKILLS_DIR) -> None:
        self._directory = directory
        self._skills: dict[str, Skill] = {}
        self._files: dict[str, Path] = {}
        self._specs: dict[str, SkillSpec] = {}

    def register(self, spec: SkillSpec) -> None:
        """按 spec 装配技能；同一 spec 可重复登记，冲突的 spec 直接拒绝。"""
        if not spec.name:
            raise ValueError("技能名不能为空")
        registered = self._specs.get(spec.name)
        if registered is not None and registered != spec:
            raise ValueError(f"技能 {spec.name} 已注册且配置不一致")
        path = self._directory / f"{spec.file}.md"
        manual = _read_manual(path)
        self._skills[spec.name] = Skill(
            spec.name,
            _head_note(manual, path.name),
            spec.tools,
            spec.keywords,
            manual,
            spec.planner,
        )
        self._files[spec.name] = path
        self._specs[spec.name] = spec

    def route(self, task: str) -> Skill | None:
        """按任务内容挑选技能；topic 不参与路由（D-23），规划能力不参与路由。"""
        text = task.strip()
        if not text:
            return None
        routable = [skill for skill in self._skills.values() if not skill.planner]
        best: tuple[int, int, Skill] | None = None
        for order, skill in enumerate(routable):
            score = sum(len(keyword) for keyword in skill.keywords if keyword and keyword in text)
            if score == 0:
                continue
            candidate = (score, -order, skill)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        if best is not None:
            return best[2]
        # 任务里直接点名技能时兜底；关键词优先，避免名字里常见的字压过意图。
        for skill in routable:
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
            if not skill.planner
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
        """主 agent 只见能力名，说明书由子代理自行查阅；内部规划能力不暴露。"""
        return [
            CapabilityDescription(name=skill.name, description="")
            for skill in self._skills.values()
            if not skill.planner
        ]


def _read_manual(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"技能说明书 {path.name} 无法读取：{exc}") from exc


def _head_note(text: str, name: str) -> str:
    """取说明书开头以 > 起头的连续行作为技能说明，正文由子代理查阅。"""
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
        raise SkillError(f"技能说明书 {name} 缺少以 {HEAD_NOTE_PREFIX} 开头的说明行")
    return note


def parse_head_note(path: Path) -> str:
    return _head_note(_read_manual(path), path.name)


MEMORY_WRITE_SKILL = "记忆写入"
MEMORY_RETRIEVE_SKILL = "记忆检索"
DRAFT_NOTE_SKILL = "自记笔记"
TASK_SKILL = "任务"
TASK_TOOLS = READ_ONLY_TOOLS
MEMORY_WRITE_TOOLS = ("index", "read", "grep", "memory")
MEMORY_RETRIEVE_TOOLS = ("index", "read", "grep")
DRAFT_NOTE_TOOLS = (
    "index",
    "read",
    "grep",
    "append",
    "insert",
    "replace_lines",
    "replace_regex",
    "delete_lines",
)
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


def task_skill() -> SkillSpec:
    """固定交接的任务规划能力；由派发执行器使用，不参与路由与能力清单。"""
    return SkillSpec(TASK_SKILL, "planning", TASK_TOOLS, planner=True)
