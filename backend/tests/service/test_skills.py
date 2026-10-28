import pytest

from service.agent.skills import MEMORY_WRITE_CAPABILITY, Skill, SkillRegistry, memory_write_skill


def make_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(Skill(name="写笔记", description="记到便签上", tools=("note",)))
    registry.register(Skill(name="翻阅", description="查看历史"))
    return registry


def test_register_and_get():
    registry = make_registry()
    skill = registry.get("写笔记")
    assert skill is not None
    assert skill.tools == ("note",)
    assert registry.get("不存在") is None


def test_duplicate_name_is_rejected():
    registry = make_registry()
    with pytest.raises(ValueError, match="已注册"):
        registry.register(Skill(name="写笔记", description="x"))


def test_empty_name_is_rejected():
    registry = SkillRegistry()
    with pytest.raises(ValueError):
        registry.register(Skill(name="", description="x"))


def test_summaries_are_capability_descriptions():
    summaries = make_registry().summaries()
    assert [(item.name, item.description) for item in summaries] == [
        ("写笔记", "记到便签上"),
        ("翻阅", "查看历史"),
    ]


def test_memory_write_skill_describes_when_to_use_it():
    skill = memory_write_skill()
    assert skill.name == MEMORY_WRITE_CAPABILITY
    assert skill.tools == ("memory_index", "memory_read", "memory_create", "memory_update")
    for hint in ("记住", "记一下"):
        assert hint in skill.description
    assert "memory_create" in skill.description
    assert "memory_update" in skill.description
