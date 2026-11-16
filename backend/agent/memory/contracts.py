import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from agent.constants import DEFAULT_PERSONA_ID

DailyLogRole = Literal["master", "me", "assistant", "system", "event", "回忆"]
ROLES: frozenset[str] = frozenset(("master", "me", "assistant", "system", "event", "回忆"))
CHAT_ROLES: frozenset[str] = frozenset(("master", "me", "assistant"))

# 回忆按「人设 + 日期」唯一，属于当天的旁注，不进聊天时间线。
RECOLLECTION_ROLE: DailyLogRole = "回忆"


@dataclass(frozen=True)
class DailyLogRow:
    id: int
    event_time: str
    role: str
    content: str
    created_at: str
    persona_id: str = DEFAULT_PERSONA_ID


@dataclass(frozen=True)
class DailyLogRecord:
    event_time: str
    role: DailyLogRole
    content: str
    created_at: str
    persona_id: str = DEFAULT_PERSONA_ID


@dataclass(frozen=True)
class DailyLogEntry:
    event_time: datetime
    role: DailyLogRole
    content: str
    created_at: datetime
    persona_id: str = DEFAULT_PERSONA_ID
    # 写入后由存储层回填的自增身份；读回的行同样携带它，供上下文按身份定位。
    id: int | None = None


class DailyLogView(BaseModel):
    id: int
    event_time: str
    role: DailyLogRole
    content: str
    persona_id: str
    created_at: str


# 标签只做格式校验：写入前拒绝空值、超长、分隔符与非中英文数字字符；
# 不解释语义、不枚举取值，近义合并只由设置页人工执行。
TAG_MAX_CHARS = 10
TAG_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff]+$")
TAG_SEPARATORS = frozenset("、，,；;/／|｜·:：")

# 正文格式（R12，由第六节第 24 条 md 拼接方式推导）：单行、≤30 字（上限可调），
# 禁换行符/制表符/顿号，禁行首 Markdown 结构字符；违规拒绝整条，不静默截断或转义。
CONTENT_MAX_CHARS = 30
CONTENT_FORBIDDEN_CHARS = "\n\r\t、"
CONTENT_FORBIDDEN_PREFIX = "#/-/>*"

MemoryState = Literal["enabled", "disabled"]
STATES: frozenset[str] = frozenset(("enabled", "disabled"))


def validate_tag(value: str) -> str:
    """校验单个 tag 的格式并返回去空白后的写法；语义仍由数据自身决定。"""
    tag = value.strip()
    if not tag:
        raise ValueError("标签不能为空")
    if len(tag) > TAG_MAX_CHARS:
        raise ValueError(f"标签 {tag!r} 超过 {TAG_MAX_CHARS} 字")
    if any(separator in tag for separator in TAG_SEPARATORS):
        raise ValueError(f"标签 {tag!r} 不能包含分隔符")
    if TAG_PATTERN.fullmatch(tag) is None:
        raise ValueError(f"标签 {tag!r} 只能包含中文、英文与数字")
    return tag


def normalize_tag(value: str) -> str:
    """复用判据：只抹平空白与大小写，不做近义归并（Q10）。"""
    return value.strip().casefold()


def checked_tags(values: list[str]) -> list[str]:
    """逐项校验并按归一化结果去重；顺序保留第一次出现。"""
    tags: list[str] = []
    seen: set[str] = set()
    for raw in values:
        tag = validate_tag(raw)
        key = normalize_tag(tag)
        if key not in seen:
            seen.add(key)
            tags.append(tag)
    if not tags:
        raise ValueError("至少需要一个标签")
    return tags


def validate_content(value: str) -> str:
    """校验正文格式并返回去首尾空白后的写法（R12）。

    先在原始值上查禁字符：换行/制表/顿号一律拒绝，不靠 strip 静默抹掉行尾换行；
    再去空白判非空、限长、查行首 Markdown 结构字符。合法正文原样保留、不转义。
    """
    if any(char in value for char in CONTENT_FORBIDDEN_CHARS):
        raise ValueError("条目内容不能包含换行符、制表符或顿号")
    content = value.strip()
    if not content:
        raise ValueError("条目内容不能为空")
    if len(content) > CONTENT_MAX_CHARS:
        raise ValueError(f"条目内容超过 {CONTENT_MAX_CHARS} 字")
    if content[0] in CONTENT_FORBIDDEN_PREFIX:
        raise ValueError("条目内容不能以 Markdown 结构字符 #/-/>/* 开头")
    return content


@dataclass(frozen=True)
class MemoryEntry:
    """条目读回形态：status 拆两列在仓储边界拼回一个对象；revision 供过期编辑冲突检测。"""

    id: int
    content: str
    tags: tuple[str, ...]
    source_ref: str | None
    created_at: str
    updated_at: str
    status_state: MemoryState
    status_reason: str | None = None
    revision: int = 1


@dataclass(frozen=True)
class TagMergeResult:
    source: str
    target: str
    affected: int
    target_existed: bool


@dataclass(frozen=True)
class TagPathChange:
    """单条条目在 tag 合并/改名前后的有序路径（Q27/第 29 条预览）。

    ``before``/``after`` 是完整有序 tags：一级标题变化看首元素，去重折叠看长度收缩，
    由前端据此标注；后端只给出确定性路径，不解释语义。
    """

    entry_id: int
    content: str
    before: tuple[str, ...]
    after: tuple[str, ...]


@dataclass(frozen=True)
class TagMergePreview:
    """合并/改名前的逐条有序路径预览：只读、不写库，供设置页阻断式确认。"""

    source: str
    target: str
    target_existed: bool
    affected: int
    changes: tuple[TagPathChange, ...]


class MemoryEntryCreate(BaseModel):
    """条目写入契约：AI 的 add 与存量转换导入共用同一套标签规则。"""

    content: str
    tags: list[str]
    source_ref: str | None = None

    @field_validator("content")
    @classmethod
    def _check_content(cls, value: str) -> str:
        return validate_content(value)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, values: list[str]) -> list[str]:
        return checked_tags(values)


class MemoryEntryUpdate(BaseModel):
    """人工修改契约（T-08 使用）；状态变更走 enable / disable，不在这里混用。"""

    content: str | None = None
    tags: list[str] | None = None

    @field_validator("content")
    @classmethod
    def _check_content(cls, value: str | None) -> str | None:
        return None if value is None else validate_content(value)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else checked_tags(values)

    @model_validator(mode="after")
    def _require_change(self) -> "MemoryEntryUpdate":
        if self.content is None and self.tags is None:
            raise ValueError("至少需要修改 content、tags 中的一项")
        return self
