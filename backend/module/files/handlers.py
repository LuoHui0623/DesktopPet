"""虚拟资源处理器：能力清单、结构化记忆与永久记忆的读取与检索。

真实文件的索引、读取与检索由服务层统一处理（需要会话、占用与队列），
这里只保留不涉及文件内容的三个只读领域。文件句柄一律交给处理器之外。
"""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta, tzinfo
from typing import Protocol

from constants.files import PERMANENT, STRUCTURED
from infra.llm.tokens import TokenCounter, TokenCountError, create_counter
from module.agent.context import TaskContext
from module.agent.skills import SkillRegistry
from module.files.format import day_text, moment, parse_moment, render_permanent
from module.files.resolver import FileError
from module.files.text import grep_lines, numbered
from module.files.views import Listing
from module.memory.daily_log_service import DailyLogService
from module.memory.entry_service import MemoryEntryService, render_entry_lines

GREP_LIMIT = 100


@dataclass(frozen=True)
class ModelLimit:
    max_tokens: int
    tokenizer: str


class ReadLimiter:
    """永久记忆读取的 token 上限来源与计数器缓存。"""

    def __init__(
        self,
        limit_provider: Callable[[], ModelLimit],
        *,
        counter_factory: Callable[[str], TokenCounter] | None = None,
    ) -> None:
        self._limit_provider = limit_provider
        self._counter_factory = counter_factory or create_counter
        self._counters: dict[str, TokenCounter] = {}
        self._lock = threading.Lock()

    def limit(self) -> ModelLimit:
        limit = self._limit_provider()
        if limit.max_tokens <= 0:
            raise FileError("子代理模型的 max_tokens 必须大于 0")
        return limit

    def counter(self, tokenizer: str) -> TokenCounter:
        """按 tokenizer 缓存计数器，避免每次读取都重新加载编码表。"""
        with self._lock:
            counter = self._counters.get(tokenizer)
            if counter is None:
                counter = self._counter_factory(tokenizer)
                self._counters[tokenizer] = counter
            return counter

    def enforce(self, content: str, label: str) -> None:
        try:
            limit = self.limit()
        except Exception as exc:
            raise FileError(f"{label}无法确定单次读取上限：{exc}") from exc
        try:
            count = self.counter(limit.tokenizer).count(content)
        except TokenCountError as exc:
            raise FileError(f"{label}的 token 计数失败，无法确认读取上限：{exc}") from exc
        if count.tokens > limit.max_tokens:
            estimate = "估算值约 " if count.estimated else "约 "
            raise FileError(
                f"{label}结果需要{estimate}{count.tokens} token，"
                f"超过单次读取上限 {limit.max_tokens} token；请缩小时间范围或先用 grep 定位"
            )


@dataclass(frozen=True)
class Query:
    """一次读取或检索的参数；各资源按需取用，缺省值表示"未指定"。"""

    name: str | None = None
    line_ranges: Sequence[Sequence[int]] | None = None
    day: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    pattern: str = ""
    limit: int = GREP_LIMIT


@dataclass(frozen=True)
class HandlerDeps:
    """处理器需要的外部能力；顺序固定，装配时按名传参。"""

    entries: MemoryEntryService
    daily: DailyLogService
    skills: SkillRegistry
    limiter: ReadLimiter
    timezone: tzinfo
    context: Callable[[], TaskContext]


class Handler(Protocol):
    """只读领域处理器：读取返回带行号的文本，检索返回匹配行。"""

    resource: str

    def read(self, deps: HandlerDeps, query: Query) -> str: ...

    def grep(self, deps: HandlerDeps, query: Query, regex) -> str: ...


def persona(deps: HandlerDeps) -> str:
    return deps.context().persona_id


def skill_catalog(deps: HandlerDeps) -> Listing:
    return Listing(
        "SKILLS",
        [{"name": item.name, "description": item.description} for item in deps.skills.index()],
    )


def entry_lines(deps: HandlerDeps) -> list[str]:
    return render_entry_lines(deps.entries.all())


def permanent_lines(
    deps: HandlerDeps,
    *,
    day: str | None,
    start_at: str | None,
    end_at: str | None,
) -> list[str]:
    """按日期或时间闭区间取当前人设的记忆，统一渲染成单行后编号。"""
    name = persona(deps)
    if day is not None:
        if start_at is not None or end_at is not None:
            raise FileError("date 与 from/to 不能同时使用")
        views = deps.daily.day_views(day_text(day), persona_id=name)
        views = [view for view in views if view.persona_id == name]
        ordered = sorted(views, key=lambda view: (moment(view.created_at), view.id))
        label = day
    else:
        if start_at is None and end_at is None:
            raise FileError("读取永久记忆需要 date 或 from/to 指定时间范围")
        start = parse_moment(start_at, deps.timezone) if start_at else None
        end = parse_moment(end_at, deps.timezone) if end_at else None
        if start is not None and end is not None and start > end:
            raise FileError("from 不能晚于 to")
        date_from = (start - timedelta(days=1)).date().isoformat() if start else None
        date_to = (end + timedelta(days=1)).date().isoformat() if end else None
        views = deps.daily.search(date_from=date_from, date_to=date_to, persona_id=name)
        chosen = []
        for view in views:
            at = moment(view.created_at).astimezone(deps.timezone)
            if start is not None and at < start:
                continue
            if end is not None and at > end:
                continue
            chosen.append((at, view))
        chosen.sort(key=lambda item: (item[0], item[1].id))
        ordered = [view for _, view in chosen]
        label = f"{start_at or '最早'} 至 {end_at or '最晚'}"
    if not ordered:
        raise FileError(f"{label} 没有当前人设的对话记录")
    return [render_permanent(view, deps.timezone) for view in ordered]


class SkillHandler:
    """能力清单：只提供说明书读取，不支持检索。"""

    resource = "skill"

    def read(self, deps: HandlerDeps, query: Query) -> str:
        if not query.name:
            raise FileError("读取技能说明书需要 name；先用 index 查看 SKILLS 清单")
        return deps.skills.read(query.name)

    def grep(self, deps: HandlerDeps, query: Query, regex) -> str:
        raise FileError("能力清单不支持检索")


class PermanentHandler:
    """永久记忆：只读，且受模型 max_tokens 约束。"""

    resource = PERMANENT

    def read(self, deps: HandlerDeps, query: Query) -> str:
        source = permanent_lines(deps, day=query.day, start_at=query.start_at, end_at=query.end_at)
        content = numbered(source, query.line_ranges)
        deps.limiter.enforce(content, "永久记忆读取")
        return content

    def grep(self, deps: HandlerDeps, query: Query, regex) -> str:
        source = permanent_lines(deps, day=query.day, start_at=query.start_at, end_at=query.end_at)
        content = grep_lines(source, regex, query.line_ranges, query.limit)
        deps.limiter.enforce(content, "永久记忆检索")
        return content


class StructuredHandler:
    """结构化记忆：条目由服务维护，工具侧只读。"""

    resource = STRUCTURED

    def read(self, deps: HandlerDeps, query: Query) -> str:
        return numbered(entry_lines(deps), query.line_ranges)

    def grep(self, deps: HandlerDeps, query: Query, regex) -> str:
        return grep_lines(entry_lines(deps), regex, query.line_ranges, query.limit)


ROUTES: dict[str, Handler] = {
    "skill": SkillHandler(),
    PERMANENT: PermanentHandler(),
    STRUCTURED: StructuredHandler(),
}
