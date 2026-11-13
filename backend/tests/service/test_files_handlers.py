"""虚拟资源处理器：路由表、请求参数、限流器与只读语义。"""

import re
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from constants.files import PERMANENT, STRUCTURED
from infra.llm.tokens import TokenCount, TokenCountError
from module.agent.context import TaskContext
from module.agent.skills import SkillRegistry, draft_note_skill
from module.files.handlers import (
    GREP_LIMIT,
    ROUTES,
    HandlerDeps,
    ModelLimit,
    Query,
    ReadLimiter,
)
from module.files.permissions import system_permissions
from module.files.resolver import FileError
from module.files.text import numbered
from module.memory.memory_service import number_lines, section

TZ = ZoneInfo("Asia/Shanghai")
TASK = TaskContext(
    persona_id="芙芙",
    persona_version=1,
    main_task_id="main-1",
    subtask_id="sub-1",
    turn_id="turn-1",
    source="user",
    permissions=system_permissions(),
)


class FixedCounter:
    def __init__(self, tokens: int, estimated: bool = False) -> None:
        self._tokens = tokens
        self._estimated = estimated

    def count(self, messages) -> TokenCount:
        return TokenCount(self._tokens, self._estimated)


class FailingCounter:
    def count(self, messages) -> TokenCount:
        raise TokenCountError("计数不可用")


class FakeEntries:
    def all(self) -> list:
        return [
            SimpleNamespace(
                content="名字：小李",
                tags=["主人信息"],
                status_state="enabled",
                status_reason=None,
            ),
            SimpleNamespace(
                content="工作地点：杭州",
                tags=["主人信息"],
                status_state="enabled",
                status_reason=None,
            ),
        ]


class FakeDaily:
    def day_views(self, day, persona_id):
        return []


def make_limiter(max_tokens: int = 10000, counter=None, tokenizer: str = "estimate") -> ReadLimiter:
    return ReadLimiter(
        lambda: ModelLimit(max_tokens, tokenizer),
        counter_factory=(lambda _name: counter) if counter is not None else None,
    )


def make_deps(**overrides) -> HandlerDeps:
    base = {
        "entries": FakeEntries(),
        "daily": FakeDaily(),
        "skills": SkillRegistry(),
        "limiter": make_limiter(),
        "timezone": TZ,
        "context": lambda: TASK,
    }
    base.update(overrides)
    return HandlerDeps(**base)


def test_routes_cover_only_virtual_resources():
    assert set(ROUTES) == {"skill", PERMANENT, STRUCTURED}
    for resource_id, handler in ROUTES.items():
        assert handler.resource == resource_id
        for verb in ("read", "grep"):
            assert callable(getattr(handler, verb)), f"{resource_id} 缺少 {verb}"


def test_handler_deps_exposes_named_dependencies():
    assert tuple(HandlerDeps.__dataclass_fields__) == (
        "entries",
        "daily",
        "skills",
        "limiter",
        "timezone",
        "context",
    )


def test_query_defaults_mirror_tool_arguments():
    query = Query()
    assert query.name is None
    assert query.line_ranges is None
    assert query.day is None
    assert query.start_at is None
    assert query.end_at is None
    assert query.pattern == ""
    assert query.limit == GREP_LIMIT


def test_read_limiter_rejects_non_positive_max_tokens():
    with pytest.raises(FileError, match="max_tokens 必须大于 0"):
        make_limiter(max_tokens=0).limit()


def test_read_limiter_caches_counter_per_tokenizer():
    names: list[str] = []

    def factory(name: str) -> FixedCounter:
        names.append(name)
        return FixedCounter(1)

    limiter = ReadLimiter(lambda: ModelLimit(10, "estimate"), counter_factory=factory)
    assert limiter.counter("estimate") is limiter.counter("estimate")
    limiter.counter("cl100k_base")
    assert names == ["estimate", "cl100k_base"]


def test_read_limiter_enforce_passes_within_limit():
    make_limiter(max_tokens=10, counter=FixedCounter(10)).enforce("内容", "永久记忆读取")


def test_read_limiter_enforce_reports_estimated_counts():
    limiter = make_limiter(max_tokens=100, counter=FixedCounter(101, estimated=True))
    with pytest.raises(FileError) as error:
        limiter.enforce("内容", "永久记忆读取")
    assert "估算值约 101 token" in str(error.value)
    assert "超过单次读取上限 100 token" in str(error.value)


def test_read_limiter_enforce_reports_exact_counts():
    limiter = make_limiter(max_tokens=100, counter=FixedCounter(101))
    with pytest.raises(FileError) as error:
        limiter.enforce("内容", "永久记忆读取")
    assert "结果需要约 101 token" in str(error.value)
    assert "估算值" not in str(error.value)


def test_read_limiter_enforce_wraps_count_failure():
    limiter = make_limiter(max_tokens=100, counter=FailingCounter())
    with pytest.raises(FileError, match="token 计数失败"):
        limiter.enforce("内容", "永久记忆读取")


def test_skill_read_returns_manual():
    skills = SkillRegistry()
    skill = draft_note_skill()
    skills.register(skill)
    deps = make_deps(skills=skills)
    assert ROUTES["skill"].read(deps, Query(name=skill.name)) == skills.read(skill.name)


def test_skill_read_requires_a_name():
    with pytest.raises(FileError, match="需要 name"):
        ROUTES["skill"].read(make_deps(), Query())


def test_skill_grep_is_rejected():
    with pytest.raises(FileError, match="能力清单不支持检索"):
        ROUTES["skill"].grep(make_deps(), Query(pattern="草稿"), None)


def test_structured_read_renders_tagged_markdown():
    text = ROUTES[STRUCTURED].read(make_deps(), Query())
    assert text.startswith("1: ## 主人信息")
    assert "2: - 名字：小李" in text
    assert "3: - 工作地点：杭州" in text


def test_structured_grep_matches_numbered_lines():
    text = ROUTES[STRUCTURED].grep(make_deps(), Query(pattern="杭州"), re.compile("杭州"))
    assert "3: - 工作地点：杭州" in text


def test_structured_read_honours_line_ranges():
    assert ROUTES[STRUCTURED].read(make_deps(), Query(line_ranges=[[1, 1]])) == "1: ## 主人信息"


def test_permanent_read_requires_a_range():
    with pytest.raises(FileError, match="需要 date 或 from/to"):
        ROUTES[PERMANENT].read(make_deps(), Query())


def test_numbered_matches_memory_numbering():
    source = ["名字：小李", "工作地点：杭州"]
    assert numbered(source, None) == number_lines(source)
    assert numbered(source, [[2, 2]]) == section(source, [[2, 2]])
