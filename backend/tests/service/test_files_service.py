import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from constants.files import (
    DRAFT,
    PERMANENT,
    RESOURCES,
    STRUCTURED,
    WORLD,
)
from infra.llm.tokens import TokenCount, TokenCountError
from infra.store.db import connect
from infra.store.migrate import migrate
from module.agent.skills import (
    SkillRegistry,
    draft_note_skill,
    memory_retrieve_skill,
    memory_write_skill,
)
from module.files.resolver import ResourceResolver
from module.files.service import FileService, ModelLimit
from module.memory.contracts import DailyLogRecord
from module.memory.daily_log_service import DailyLogService
from module.memory.index import MemoryIndexService
from module.memory.memory_service import StructuredMemoryDoc
from module.memory.repository import DbDailyLog
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")


class FixedCounter:
    def __init__(self, tokens: int, estimated: bool = False) -> None:
        self._tokens = tokens
        self._estimated = estimated

    def count(self, messages) -> TokenCount:
        return TokenCount(self._tokens, self._estimated)


class FailingCounter:
    def count(self, messages) -> TokenCount:
        raise TokenCountError("计数不可用")


def record(event_time: str, role: str, content: str, persona: str = "芙芙") -> DailyLogRecord:
    return DailyLogRecord(
        event_time=event_time,
        role=role,
        content=content,
        created_at=event_time,
        persona_id=persona,
    )


@pytest.fixture
def stack(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    data = tmp_path / "data"
    (data / "resources").mkdir(parents=True)
    (data / "memory").mkdir()
    (data / "resources" / "草稿本.md").write_text("# 草稿本\n", encoding="utf-8")
    structured = StructuredMemoryDoc(data / "memory" / "structured.md")
    structured.save("名字：小李\n工作地点：杭州\n")
    settings = SettingsService(db_file)
    daily = DailyLogService(DbDailyLog(db_file), settings, TZ)
    index = MemoryIndexService(structured.lines, daily.dates, data / "memory" / "index.json")
    structured.add_listener(index.rebuild)
    index.rebuild()
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    skills.register(memory_retrieve_skill())
    skills.register(draft_note_skill())

    def build(
        *,
        max_tokens: int = 10000,
        counter=None,
        tokenizer: str = "estimate",
    ) -> FileService:
        return FileService(
            ResourceResolver(data),
            structured,
            daily,
            skills,
            timezone=TZ,
            limit_provider=lambda: ModelLimit(max_tokens, tokenizer),
            counter_factory=(lambda _name: counter) if counter is not None else None,
        )

    return {
        "tmp": tmp_path,
        "data": data,
        "db_file": db_file,
        "structured": structured,
        "daily": daily,
        "index_file": data / "memory" / "index.json",
        "service": build(),
        "build": build,
        "store": DbDailyLog(db_file),
    }


def test_index_lists_resources_actions_and_skills(stack):
    payload = json.loads(stack["service"].index())
    assert [item["id"] for item in payload["resources"]] == [
        DRAFT,
        STRUCTURED,
        PERMANENT,
        WORLD,
    ]
    by_id = {item["id"]: item for item in payload["resources"]}
    for resource_id, spec in RESOURCES.items():
        assert by_id[resource_id]["actions"] == sorted(spec.actions)
        assert by_id[resource_id]["can_delete_file"] is spec.can_delete_file
    assert {"read", "grep", "edit", "append", "insert", "replace", "delete"} <= set(
        payload["actions"]
    )
    assert {item["name"] for item in payload["skills"]} >= {"记忆写入", "记忆检索", "自记笔记"}
    assert "expect" in payload["parameters"]
    assert "from/to" in payload["parameters"]
    assert "max_tokens" in payload["limits"]
    assert payload["persona"] == "芙芙"


def test_index_world_returns_tree_and_metadata(stack):
    (stack["data"] / "resources" / "笔记.md").write_text("便签", encoding="utf-8")
    payload = json.loads(stack["service"].index(WORLD))
    assert payload["resource"] == WORLD
    paths = {item["path"] for item in payload["entries"]}
    assert "草稿本.md" in paths
    assert "笔记.md" in paths


def test_index_structured_reports_lines_and_headings(stack):
    stack["structured"].save("## 主人信息\n名字：小李\n")
    payload = json.loads(stack["service"].index(STRUCTURED))
    assert payload["lines"] == 2
    assert payload["headings"] == [{"line": 1, "text": "## 主人信息"}]


def test_index_permanent_reports_persona_and_dates(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "早")])
    payload = json.loads(stack["service"].index(PERMANENT))
    assert payload["resource"] == PERMANENT
    assert payload["persona"] == "芙芙"
    assert "2026-10-18" in payload["dates"]
    assert payload["limit"]["max_tokens"] == 10000


def test_read_files_returns_numbered_lines_and_ranges(stack):
    (stack["data"] / "resources" / "笔记.md").write_text("一\n二\n三", encoding="utf-8")
    assert stack["service"].read(WORLD, "笔记.md") == "1: 一\n2: 二\n3: 三"
    assert stack["service"].read(WORLD, "笔记.md", lines=[[2, 3]]) == "2: 二\n3: 三"


def test_read_structured_and_skills(stack):
    assert stack["service"].read(STRUCTURED) == "1: 名字：小李\n2: 工作地点：杭州"
    assert stack["service"].read("skill", name="记忆检索").startswith("# 记忆检索")


def test_read_missing_file_is_reported(stack):
    with pytest.raises(Exception, match="文件不存在"):
        stack["service"].read(WORLD, "缺失.md")


def test_append_creates_world_file_and_reports_lines(stack):
    message = stack["service"].append(WORLD, "新文件内容\n第二行", path="新文件.md")
    assert "1-2" in message
    assert (stack["data"] / "resources" / "新文件.md").read_text(encoding="utf-8") == (
        "新文件内容\n第二行\n"
    )


def test_append_draft_keeps_existing_content(stack):
    stack["service"].append(DRAFT, "待办：续费")
    assert (stack["data"] / "resources" / "草稿本.md").read_text(encoding="utf-8") == (
        "# 草稿本\n待办：续费\n"
    )


def test_structured_writes_refresh_index(stack):
    stack["service"].append(STRUCTURED, "咖啡偏好\n喜欢美式")
    index = json.loads(stack["index_file"].read_text(encoding="utf-8"))
    assert index["index"]["structured"]["lines"] == 4


def test_structured_write_keeps_sensitive_guard_and_ignores_legacy_temporary_setting(stack):
    with pytest.raises(Exception, match="密码"):
        stack["service"].append(STRUCTURED, "网银密码是 abc")
    SettingsService(stack["db_file"]).put("memory.temporary_mode", "true")
    stack["service"].delete(STRUCTURED, lines=[[1]])
    assert stack["structured"].lines() == ["工作地点：杭州"]


def test_insert_places_content_before_the_line(stack):
    stack["service"].insert(STRUCTURED, 2, "新的一行")
    assert stack["structured"].lines() == ["名字：小李", "新的一行", "工作地点：杭州"]
    stack["service"].insert(STRUCTURED, 4, "末尾")
    assert stack["structured"].lines()[-1] == "末尾"


def test_insert_out_of_range_is_rejected(stack):
    with pytest.raises(Exception, match="插入位置"):
        stack["service"].insert(STRUCTURED, 9, "越界")


def test_edit_replaces_multiple_ranges_and_checks_expectation(stack):
    message = stack["service"].edit(
        STRUCTURED,
        lines=[[1], [2]],
        content="已替换",
        expect=["名字：小李", "工作地点：杭州"],
    )
    assert "1-2" in message
    assert stack["structured"].lines() == ["已替换", "已替换"]


def test_edit_expect_mismatch_does_not_write(stack):
    with pytest.raises(Exception, match="expect"):
        stack["service"].edit(STRUCTURED, lines=[[1]], content="新", expect="旧")
    assert stack["structured"].lines() == ["名字：小李", "工作地点：杭州"]


def test_replace_matches_regex_inside_ranges(stack):
    stack["service"].replace(STRUCTURED, pattern="杭州", content="上海", lines=[[2]])
    assert stack["structured"].lines()[1] == "工作地点：上海"
    with pytest.raises(Exception, match="没有匹配"):
        stack["service"].replace(STRUCTURED, pattern="北京", content="深圳", lines=[[1, 2]])


def test_delete_lines_keeps_the_file(stack):
    stack["service"].delete(STRUCTURED, lines=[[2]])
    assert stack["structured"].lines() == ["名字：小李"]
    assert stack["structured"].path.exists()


def test_delete_expect_mismatch_does_not_write(stack):
    with pytest.raises(Exception, match="expect"):
        stack["service"].delete(STRUCTURED, lines=[[2]], expect="旧内容")
    assert stack["structured"].lines() == ["名字：小李", "工作地点：杭州"]


def test_delete_world_file_is_allowed_but_draft_and_structured_are_not(stack):
    target = stack["data"] / "resources" / "笔记.md"
    target.write_text("便签", encoding="utf-8")
    stack["service"].delete(WORLD, path="笔记.md")
    assert not target.exists()

    with pytest.raises(Exception, match="不能删除文件本体"):
        stack["service"].delete(DRAFT)
    with pytest.raises(Exception, match="不能删除文件本体"):
        stack["service"].delete(STRUCTURED)
    assert (stack["data"] / "resources" / "草稿本.md").exists()
    assert stack["structured"].path.exists()


def test_world_cannot_bypass_draft_file_delete_rule(stack):
    with pytest.raises(Exception, match="不能删除文件本体"):
        stack["service"].delete(WORLD, path="草稿本.md")


def test_permanent_read_by_date_uses_active_persona(stack):
    stack["store"].append(
        [
            record("2026-10-18T09:00:00+08:00", "master", "喜欢美式"),
            record("2026-10-18T09:00:01+08:00", "assistant", "查到：常点美式"),
            record("2026-10-18T09:00:02+08:00", "me", "记住啦"),
            record("2026-10-18T09:00:03+08:00", "master", "别人说的", "其他人设"),
            record("2026-10-18T09:00:04+08:00", "event", '{"type":"x"}'),
        ]
    )
    text = stack["service"].read(PERMANENT, day="2026-10-18")
    assert "[master]喜欢美式" in text
    assert "[assistant]查到：常点美式" in text
    assert "[me]记住啦" in text
    assert "别人说的" not in text
    assert "[event]事件：" in text
    assert text.splitlines()[0].startswith("1: [2026-10-18 09:00:00]")


def test_permanent_range_is_inclusive_and_normalizes_offsets(stack):
    stack["store"].append(
        [
            record("2026-10-18T00:30:00+09:00", "master", "早于开始"),
            record("2026-10-18T09:00:00+08:00", "master", "开始"),
            record("2026-10-18T09:30:00+08:00", "master", "中间"),
            record("2026-10-18T10:00:00+08:00", "master", "结束"),
            record("2026-10-18T10:00:01+08:00", "master", "晚于结束"),
        ]
    )
    text = stack["service"].read(
        PERMANENT,
        start_at="2026-10-18T09:00:00",
        end_at="2026-10-18T10:00:00+08:00",
    )
    assert "开始" in text
    assert "中间" in text
    assert "结束" in text
    assert "早于开始" not in text
    assert "晚于结束" not in text
    # +09:00 的 00:30 即 +08:00 的前一天 23:30，归一化后不应落在当天上午
    assert "23:30:00" not in text


def test_permanent_empty_range_is_reported(stack):
    with pytest.raises(Exception, match="没有.*记录"):
        stack["service"].read(PERMANENT, day="2026-10-01")


def test_permanent_read_requires_a_time_window(stack):
    with pytest.raises(Exception, match="date 或 from/to"):
        stack["service"].read(PERMANENT)


def test_permanent_read_allows_exact_token_limit_and_rejects_more(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "早")])
    equal = stack["build"](max_tokens=10, counter=FixedCounter(10))
    assert equal.read(PERMANENT, day="2026-10-18")

    over = stack["build"](max_tokens=10, counter=FixedCounter(11, estimated=True))
    with pytest.raises(Exception, match="超过") as exc:
        over.read(PERMANENT, day="2026-10-18")
    assert "估算" in str(exc.value)


def test_permanent_read_reports_token_count_failure(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "早")])
    service = stack["build"](counter=FailingCounter())
    with pytest.raises(Exception, match="计数失败"):
        service.read(PERMANENT, day="2026-10-18")


def test_permanent_is_read_only(stack):
    with pytest.raises(Exception, match="不支持"):
        stack["service"].append(PERMANENT, "x")
    with pytest.raises(Exception, match="不支持"):
        stack["service"].delete(PERMANENT)


def test_grep_searches_file_and_world_recursively(stack):
    (stack["data"] / "resources" / "笔记.md").write_text("苹果\n香蕉\n苹果派", encoding="utf-8")
    assert stack["service"].grep("苹果", WORLD, "笔记.md") == "1: 苹果\n3: 苹果派"
    (stack["data"] / "resources" / "子目录").mkdir()
    (stack["data"] / "resources" / "子目录" / "更多.md").write_text("苹果汁", encoding="utf-8")
    world = stack["service"].grep("苹果", WORLD)
    assert "笔记.md:1: 苹果" in world
    assert "子目录/更多.md:1: 苹果汁" in world


def test_grep_permanent_and_invalid_pattern(stack):
    stack["store"].append([record("2026-10-18T09:00:00+08:00", "master", "喜欢美式")])
    text = stack["service"].grep("美式", PERMANENT, day="2026-10-18")
    assert "美式" in text
    with pytest.raises(Exception, match="正则"):
        stack["service"].grep("(", WORLD)


def test_write_outside_whitelist_is_rejected(stack, tmp_path: Path):
    outside = tmp_path / "outside.md"
    with pytest.raises(Exception, match="不在授权资源内"):
        stack["service"].append(WORLD, "越界", path=str(outside))
    assert not outside.exists()
