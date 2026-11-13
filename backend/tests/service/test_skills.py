import pytest

from constants.agent import READ_ONLY_TOOLS
from module.agent.orchestrator import read_only_tools
from module.agent.skills import (
    DRAFT_NOTE_SKILL,
    DRAFT_NOTE_TOOLS,
    MEMORY_RETRIEVE_SKILL,
    MEMORY_RETRIEVE_TOOLS,
    MEMORY_WRITE_SKILL,
    MEMORY_WRITE_TOOLS,
    TASK_SKILL,
    Skill,
    SkillError,
    SkillRegistry,
    SkillSpec,
    draft_note_skill,
    memory_retrieve_skill,
    memory_write_skill,
    parse_head_note,
    task_skill,
)


def write_manual(directory, name: str, text: str):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def make_registry(tmp_path) -> SkillRegistry:
    write_manual(
        tmp_path,
        "note",
        "# 写笔记\n\n> 把要点记到便签上。\n> 主人提到别忘了的事情时使用。\n\n## 流程\n",
    )
    write_manual(tmp_path, "browse", "# 翻阅\n\n> 查看历史记录。\n")
    registry = SkillRegistry(tmp_path)
    registry.register(SkillSpec("写笔记", "note", ("note",), ("记下",)))
    registry.register(SkillSpec("翻阅", "browse"))
    return registry


def test_register_and_get(tmp_path):
    registry = make_registry(tmp_path)
    skill = registry.get("写笔记")
    assert skill is not None
    assert skill.tools == ("note",)
    assert registry.get("不存在") is None


def test_duplicate_name_is_rejected(tmp_path):
    registry = make_registry(tmp_path)
    with pytest.raises(ValueError, match="已注册"):
        registry.register(SkillSpec("写笔记", "note"))


def test_registering_the_same_spec_twice_is_idempotent(tmp_path):
    registry = make_registry(tmp_path)
    registry.register(SkillSpec("写笔记", "note", ("note",), ("记下",)))
    assert registry.names() == ("写笔记", "翻阅")
    assert registry.get("写笔记").tools == ("note",)


def test_task_spec_is_hidden_from_routing_and_listing(tmp_path):
    write_manual(
        tmp_path,
        "planning",
        "# 任务规划\n\n> 先只读核对现状，再输出任务清单。\n\n## 执行流程\n\n1. 核对现状。\n",
    )
    write_manual(tmp_path, "note", "# 写笔记\n\n> 记到便签。\n")
    registry = SkillRegistry(tmp_path)
    registry.register(task_skill())
    registry.register(SkillSpec("写笔记", "note"))

    skill = registry.get(TASK_SKILL)
    assert skill is not None and skill.planner is True
    assert "先只读核对现状" in skill.manual
    assert registry.route("任务规划") is None
    assert [item.name for item in registry.index()] == ["写笔记"]
    assert [item.name for item in registry.summaries()] == ["写笔记"]
    assert registry.read(TASK_SKILL).startswith("# 任务规划")


def test_task_skill_only_declares_read_only_tools():
    spec = task_skill()
    assert spec.name == TASK_SKILL
    assert spec.file == "planning"
    assert spec.tools == READ_ONLY_TOOLS
    assert spec.planner is True


def test_empty_name_is_rejected(tmp_path):
    registry = SkillRegistry(tmp_path)
    with pytest.raises(ValueError):
        registry.register(SkillSpec("", "note"))


def test_description_comes_from_head_note(tmp_path):
    registry = make_registry(tmp_path)
    assert registry.get("写笔记").description == "把要点记到便签上。主人提到别忘了的事情时使用。"


def test_summaries_only_expose_names(tmp_path):
    summaries = make_registry(tmp_path).summaries()
    assert [(item.name, item.description) for item in summaries] == [
        ("写笔记", ""),
        ("翻阅", ""),
    ]


def test_index_exposes_names_and_descriptions(tmp_path):
    index = make_registry(tmp_path).index()
    assert [(item.name, item.description) for item in index] == [
        ("写笔记", "把要点记到便签上。主人提到别忘了的事情时使用。"),
        ("翻阅", "查看历史记录。"),
    ]


def test_read_returns_whole_manual(tmp_path):
    text = make_registry(tmp_path).read("写笔记")
    assert text.startswith("# 写笔记")
    assert "## 流程" in text


def test_read_unknown_skill_is_reported(tmp_path):
    with pytest.raises(SkillError, match="index"):
        make_registry(tmp_path).read("不存在")


def test_missing_manual_is_reported(tmp_path):
    registry = SkillRegistry(tmp_path)
    with pytest.raises(SkillError, match="无法读取"):
        registry.register(SkillSpec("写笔记", "note"))


def test_missing_head_note_is_reported(tmp_path):
    path = write_manual(tmp_path, "bare", "# 没有说明\n\n正文\n")
    with pytest.raises(SkillError, match="缺少"):
        parse_head_note(path)


def test_head_note_stops_at_first_body_line(tmp_path):
    path = write_manual(tmp_path, "multi", "# 标题\n\n> 第一段\n> 第二段\n\n> 这不是说明\n")
    assert parse_head_note(path) == "第一段第二段"


def test_memory_write_skill_declares_write_tools():
    spec = memory_write_skill()
    assert spec.name == MEMORY_WRITE_SKILL
    assert spec.file == "memory_write"
    assert spec.tools == MEMORY_WRITE_TOOLS


def test_draft_note_skill_declares_file_tools():
    spec = draft_note_skill()
    assert spec.name == DRAFT_NOTE_SKILL
    assert spec.file == "draft_note"
    assert spec.tools == DRAFT_NOTE_TOOLS


def test_draft_note_allows_reading_while_drafting():
    skill = Skill(DRAFT_NOTE_SKILL, "便签", DRAFT_NOTE_TOOLS)
    assert read_only_tools(skill) == ("index", "read", "grep")


def test_memory_retrieve_skill_is_read_only():
    spec = memory_retrieve_skill()
    assert spec.name == MEMORY_RETRIEVE_SKILL
    assert spec.file == "memory_retrieve"
    assert spec.tools == MEMORY_RETRIEVE_TOOLS


def test_shipped_manuals_parse():
    registry = SkillRegistry()
    registry.register(memory_write_skill())
    registry.register(memory_retrieve_skill())
    registry.register(draft_note_skill())
    registry.register(task_skill())
    assert registry.names() == (
        MEMORY_WRITE_SKILL,
        MEMORY_RETRIEVE_SKILL,
        DRAFT_NOTE_SKILL,
        TASK_SKILL,
    )
    for name in registry.names():
        assert registry.get(name).description
        assert "## 执行流程" in registry.read(name)


def test_route_matches_task_keywords_not_topic(tmp_path):
    registry = SkillRegistry()
    registry.register(memory_write_skill())
    registry.register(memory_retrieve_skill())
    registry.register(draft_note_skill())

    assert registry.route("记住主人喜欢美式咖啡") == registry.get(MEMORY_WRITE_SKILL)
    assert registry.route("回忆一下上次聊过的咖啡") == registry.get(MEMORY_RETRIEVE_SKILL)
    assert registry.route("把明天要买的东西记一下") == registry.get(DRAFT_NOTE_SKILL)


def test_route_prefers_the_longer_keyword(tmp_path):
    write_manual(tmp_path, "short", "# 短词\n\n> 短词。\n")
    write_manual(tmp_path, "long", "# 长词\n\n> 长词。\n")
    registry = SkillRegistry(tmp_path)
    registry.register(SkillSpec("短词", "short", (), ("查",)))
    registry.register(SkillSpec("长词", "long", (), ("查一下",)))
    assert registry.route("帮我查一下") == registry.get("长词")


def test_route_returns_none_when_nothing_matches(tmp_path):
    assert make_registry(tmp_path).route("随便说点什么") is None


def test_route_falls_back_to_the_skill_name(tmp_path):
    registry = make_registry(tmp_path)
    assert registry.route("帮我翻阅") == registry.get("翻阅")
