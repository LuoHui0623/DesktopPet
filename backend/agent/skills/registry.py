"""技能登记：说明书即元数据源，按标准 skill 形态读取。

形态固定为 `resources/skills/<slug>/SKILL.md`：文件头 YAML frontmatter 提供 `name` 与
`description`，其余正文是子代理按需查阅的说明书。代码侧 `SkillSpec` 只登记路由与工具
（`slug`、`tools`、`keywords`、`planner`），技能名与说明一律从 frontmatter 读取；
两处不一致时在登记时直接报错，不做静默兼容，也不回退到旧格式。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from agent.constants import READ_ONLY_TOOLS
from agent.prompts.templates import CapabilityDescription
from constants.path import SKILLS_DIR

# 说明书入口文件名固定，不随技能变化；技能目录名即 `SkillSpec.slug`。
SKILL_ENTRY = "SKILL.md"
FRONTMATTER_FENCE = "---"
FRONTMATTER_FIELDS: tuple[str, ...] = ("name", "description")


class SkillError(Exception):
    pass


@dataclass(frozen=True)
class SkillMeta:
    """一份说明书解析后的元数据与正文；正文不含 frontmatter。"""

    name: str
    description: str
    body: str


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
    """代码侧登记的路由骨架：技能目录名、可用工具与路由关键词。

    `name` 只用于登记时与 frontmatter 对齐——不一致即报错，避免「代码里一个名字、
    说明书里另一个名字」的静默漂移；对外暴露的技能名始终来自说明书。
    `planner` 标记内部任务规划能力：它由派发执行器固定使用，不参与关键词路由，
    也不出现在主代理或子代理的能力清单里。
    """

    name: str
    slug: str
    tools: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    planner: bool = False


class SkillRegistry:
    def __init__(self, directory: Path = SKILLS_DIR) -> None:
        self._directory = directory
        self._skills: dict[str, Skill] = {}
        self._files: dict[str, Path] = {}
        self._specs: dict[str, SkillSpec] = {}

    def path_of(self, slug: str) -> Path:
        """目录名 → 说明书路径；入口文件名固定为 `SKILL.md`。"""
        return self._directory / slug / SKILL_ENTRY

    def register(self, spec: SkillSpec) -> None:
        """按 spec 装配技能；同一 spec 可重复登记，冲突的 spec 直接拒绝。"""
        if not spec.name:
            raise ValueError("技能名不能为空")
        if not spec.slug:
            raise ValueError(f"技能 {spec.name} 缺少目录名")
        registered = self._specs.get(spec.name)
        if registered is not None and registered != spec:
            raise ValueError(f"技能 {spec.name} 已注册且配置不一致")
        path = self.path_of(spec.slug)
        meta = parse_skill(path)
        if meta.name != spec.name:
            raise SkillError(
                f"技能目录 {spec.slug} 的 frontmatter 声明为 {meta.name}，"
                f"与登记的 {spec.name} 不一致"
            )
        self._skills[spec.name] = Skill(
            spec.name,
            meta.description,
            spec.tools,
            spec.keywords,
            meta.body,
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
        """返回说明书正文：frontmatter 已作为元数据解析，不再重复下发给子代理。"""
        path = self._files.get(name)
        if path is None:
            raise SkillError(f"没有名为 {name} 的能力，请先用 index 查看能力清单")
        return parse_skill(path).body

    def summaries(self) -> Sequence[CapabilityDescription]:
        """主 agent 只见能力名，说明书由子代理自行查阅；内部规划能力不暴露。"""
        return [
            CapabilityDescription(name=skill.name, description="")
            for skill in self._skills.values()
            if not skill.planner
        ]


def parse_skill(path: Path) -> SkillMeta:
    """解析 `SKILL.md`：frontmatter 给元数据，其余为正文；格式不合规即报错。"""
    text = _read(path)
    label = _label(path)
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_FENCE:
        raise SkillError(
            f"技能说明书 {label} 缺少 YAML frontmatter：首行必须是 {FRONTMATTER_FENCE}"
        )
    close = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == FRONTMATTER_FENCE
        ),
        None,
    )
    if close is None:
        raise SkillError(f"技能说明书 {label} 的 frontmatter 没有闭合的 {FRONTMATTER_FENCE}")
    fields = _frontmatter(lines[1:close], label)
    body = "\n".join(lines[close + 1 :]).strip("\n")
    if not body.strip():
        raise SkillError(f"技能说明书 {label} 只有 frontmatter，没有正文")
    return SkillMeta(fields["name"], fields["description"], body)


def _frontmatter(lines: Sequence[str], label: str) -> dict[str, str]:
    try:
        raw = yaml.safe_load("\n".join(lines))
    except yaml.YAMLError as exc:
        raise SkillError(f"技能说明书 {label} 的 frontmatter 不是合法 YAML：{exc}") from exc
    if not isinstance(raw, dict):
        raise SkillError(f"技能说明书 {label} 的 frontmatter 必须是键值对")
    unknown = sorted(str(key) for key in raw if key not in FRONTMATTER_FIELDS)
    if unknown:
        raise SkillError(
            f"技能说明书 {label} 的 frontmatter 含未知字段：{'、'.join(unknown)}；"
            f"只允许 {'、'.join(FRONTMATTER_FIELDS)}"
        )
    missing = [
        field
        for field in FRONTMATTER_FIELDS
        if not isinstance(raw.get(field), str) or not raw[field].strip()
    ]
    if missing:
        raise SkillError(
            f"技能说明书 {label} 的 frontmatter 缺少 {'、'.join(missing)}（必须是非空字符串）"
        )
    return {field: raw[field].strip() for field in FRONTMATTER_FIELDS}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"技能说明书 {_label(path)} 无法读取：{exc}") from exc


def _label(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


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
