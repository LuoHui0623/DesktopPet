"""资源处理器契约：路由表、请求参数、限流器与索引载荷。"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from constants.files import (
    ACTION_READ,
    DRAFT,
    PERMANENT,
    RESOURCE_ORDER,
    STRUCTURED,
    WORLD,
)
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
    overview,
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


class FakeDaily:
    def dates(self) -> list[str]:
        return ["2026-01-02"]


class FakeEntries:
    def counts(self) -> tuple[int, int]:
        return (2, 1)

    def tags(self) -> list[str]:
        return ["主人信息"]

    def all(self) -> list:
        return []


class NoEditor:
    """处理器只经由 FileService 触达草稿层，索引与读取路径不应调用它。"""


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
        "editor": NoEditor(),
        "limiter": make_limiter(),
        "timezone": TZ,
        "context": lambda: TASK,
    }
    base.update(overrides)
    return HandlerDeps(**base)


def test_routes_cover_every_resource():
    assert set(ROUTES) == {"skill", PERMANENT, STRUCTURED, DRAFT, WORLD}
    for resource_id, handler in ROUTES.items():
        assert handler.resource == resource_id
        for verb in ("index", "read", "grep"):
            assert callable(getattr(handler, verb)), f"{resource_id} 缺少 {verb}"


def test_handler_deps_exposes_named_dependencies():
    assert tuple(HandlerDeps.__dataclass_fields__) == (
        "entries",
        "daily",
        "skills",
        "editor",
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


def test_overview_lists_every_allowed_resource():
    payload = overview(make_deps())
    assert payload["kind"] == "overview"
    assert payload["persona"] == "芙芙"
    assert [item["id"] for item in payload["resources"]] == list(RESOURCE_ORDER)
    assert payload["skills"] == []
    assert payload["next"] == ACTION_READ
    for key in ("actions", "parameters", "limits"):
        assert payload[key]


def test_permanent_index_reports_limit_and_dates():
    payload = ROUTES[PERMANENT].index(make_deps(), None, Query())
    assert payload["kind"] == "permanent"
    assert payload["persona"] == "芙芙"
    assert payload["dates"] == ["2026-01-02"]
    assert payload["read_only"] is True
    assert payload["limit"] == {"max_tokens": 10000, "tokenizer": "estimate"}


def test_permanent_index_degrades_when_model_unavailable():
    def broken() -> ModelLimit:
        raise ValueError("模型未就绪")

    deps = make_deps(limiter=ReadLimiter(broken))
    payload = ROUTES[PERMANENT].index(deps, None, Query())
    assert payload["limit"] == {"max_tokens": None, "reason": "模型未就绪"}


def test_structured_index_counts_entries():
    payload = ROUTES[STRUCTURED].index(make_deps(), None, Query())
    assert payload["kind"] == "structured"
    assert payload["entries"] == 3
    assert payload["enabled"] == 2
    assert payload["disabled"] == 1
    assert payload["tags"] == ["主人信息"]
    assert payload["read_only"] is True


def test_skill_index_lists_registered_skills():
    skills = SkillRegistry()
    skill = draft_note_skill()
    skills.register(skill)
    payload = ROUTES["skill"].index(make_deps(skills=skills), None, Query())
    assert payload["kind"] == "skills"
    assert [item["name"] for item in payload["skills"]] == [skill.name]
    assert payload["next"] == ACTION_READ


def test_skill_read_returns_manual():
    skills = SkillRegistry()
    skill = draft_note_skill()
    skills.register(skill)
    deps = make_deps(skills=skills)
    assert ROUTES["skill"].read(deps, None, Query(name=skill.name)) == skills.read(skill.name)


def test_skill_grep_is_rejected():
    with pytest.raises(FileError, match="能力清单不支持检索"):
        ROUTES["skill"].grep(make_deps(), None, Query(pattern="草稿"), None)


def test_numbered_matches_memory_numbering():
    source = ["名字：小李", "工作地点：杭州"]
    assert numbered(source, None) == number_lines(source)
    assert numbered(source, [[2, 2]]) == section(source, [[2, 2]])


def test_file_service_reexports_moved_names():
    from module.files import service

    assert service.ModelLimit is ModelLimit
    assert service.GREP_LIMIT == GREP_LIMIT
    assert callable(service.FileService)
