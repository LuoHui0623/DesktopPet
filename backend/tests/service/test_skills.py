import pytest

from service.agent.skills import Skill, SkillRegistry


def make_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        Skill(name="写笔记", description="记到便签上", keywords=("记一下", "记住"), tools=("note",))
    )
    registry.register(Skill(name="翻阅", description="查看历史", keywords=("上次说过",)))
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


def test_keyword_bound_twice_is_rejected():
    registry = make_registry()
    with pytest.raises(ValueError, match="已绑定能力 写笔记"):
        registry.register(Skill(name="另一个", description="x", keywords=("记住",)))


def test_empty_keyword_and_name_are_rejected():
    registry = SkillRegistry()
    with pytest.raises(ValueError):
        registry.register(Skill(name="", description="x"))
    with pytest.raises(ValueError):
        registry.register(Skill(name="a", description="x", keywords=("",)))


def test_keyword_match_is_case_insensitive_substring():
    registry = make_registry()
    assert [skill.name for skill in registry.match("请帮我记一下牛奶")] == ["写笔记"]
    assert [skill.name for skill in registry.match("你还记得上次说过什么吗")] == ["翻阅"]
    assert registry.match("今天天气不错") == []


def test_summaries_are_capability_descriptions():
    summaries = make_registry().summaries()
    assert [(item.name, item.description) for item in summaries] == [
        ("写笔记", "记到便签上"),
        ("翻阅", "查看历史"),
    ]
