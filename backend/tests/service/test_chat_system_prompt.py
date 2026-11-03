from string import Template

from module.agent.chat_system_prompt import (
    ACTION_RULES_BODY,
    CAPABILITIES_BODY,
    CATEGORY_BEHAVIOR,
    CATEGORY_CAPABILITY,
    CATEGORY_IDENTITY,
    CATEGORY_ORDER,
    CHAT_PLACEHOLDERS,
    CONSTRAINTS_BODY,
    PERSONA_BODY,
    RESOURCES_BODY,
    SEPARATOR_WIDTH,
    SPEECH_RULES_BODY,
    TOPIC_ACTION_RULES,
    TOPIC_BODIES,
    TOPIC_CAPABILITIES,
    TOPIC_CONSTRAINTS,
    TOPIC_PERSONA,
    TOPIC_REGISTRY,
    TOPIC_RESOURCES,
    TOPIC_SPEECH_RULES,
    TopicContext,
)

BODIES = {
    TOPIC_PERSONA.id: PERSONA_BODY,
    TOPIC_ACTION_RULES.id: ACTION_RULES_BODY,
    TOPIC_CAPABILITIES.id: CAPABILITIES_BODY,
    TOPIC_RESOURCES.id: RESOURCES_BODY,
    TOPIC_SPEECH_RULES.id: SPEECH_RULES_BODY,
    TOPIC_CONSTRAINTS.id: CONSTRAINTS_BODY,
}


def test_topic_ids_are_distinct_and_readable():
    ids = [topic.id for topic in TOPIC_REGISTRY]
    assert ids == [
        "人设",
        "可用能力",
        "可用资源",
        "行动规则",
        "发言规则",
        "约束",
    ]
    assert len(set(ids)) == len(ids)


def test_registry_maps_topics_to_the_three_categories():
    assert CATEGORY_ORDER == ("身份", "能力", "行为")
    assert [topic.category for topic in TOPIC_REGISTRY] == [
        CATEGORY_IDENTITY,
        CATEGORY_CAPABILITY,
        CATEGORY_CAPABILITY,
        CATEGORY_BEHAVIOR,
        CATEGORY_BEHAVIOR,
        CATEGORY_BEHAVIOR,
    ]


def test_registry_order_covers_every_body_exactly_once():
    assert len(TOPIC_REGISTRY) == len({topic.id for topic in TOPIC_REGISTRY})
    assert set(TOPIC_BODIES) == {topic.id for topic in TOPIC_REGISTRY}


def test_body_table_points_at_the_named_constants():
    assert TOPIC_BODIES == BODIES


def test_every_body_is_a_trimmed_non_empty_string():
    for _topic, body in BODIES.items():
        assert isinstance(body, str)
        assert body.strip()
        assert body == body.strip()


def test_bodies_carry_no_heading_of_their_own():
    for topic, body in BODIES.items():
        assert "【" not in body, topic
        assert "】" not in body, topic


def test_action_rules_describe_both_output_forms():
    assert '{"type": "chat", "text": ["要说给主人的话"]}' in ACTION_RULES_BODY
    assert '{"type": "dispatch", "plan": [' in ACTION_RULES_BODY
    assert ACTION_RULES_BODY.index("一、对话") < ACTION_RULES_BODY.index("二、派发任务")


def test_action_rules_explain_the_single_optional_dispatch():
    assert "最多一次" in ACTION_RULES_BODY
    assert "不要超过 3 项" in ACTION_RULES_BODY


def test_capability_note_is_separated_from_the_value_line():
    lines = CAPABILITIES_BODY.splitlines()
    assert lines[0] == "下面是你可以交办的事项；派发时按任务内容描述，不必指定名称。"
    assert lines[1] == "$capabilities"
    assert len(lines) == 2


def test_resources_body_is_only_the_placeholder():
    assert RESOURCES_BODY == "$resources"


def test_speech_rules_reference_every_length_limit():
    assert "$sentence_max_chars" in SPEECH_RULES_BODY
    assert "$sentence_max_width" in SPEECH_RULES_BODY
    assert "$persona_hint_chars" in SPEECH_RULES_BODY


def test_constraints_require_type_to_come_first():
    assert '"type" 必须是第一个字段' in CONSTRAINTS_BODY


def test_placeholders_are_exactly_the_documented_set():
    assert CHAT_PLACEHOLDERS == {
        "persona_section",
        "capabilities",
        "resources",
        "sentence_max_chars",
        "sentence_max_width",
        "persona_hint_chars",
    }


def test_placeholders_in_bodies_match_the_documented_set():
    found: set[str] = set()
    for body in BODIES.values():
        found.update(Template(body).get_identifiers())
    assert found == set(CHAT_PLACEHOLDERS)


def test_every_placeholder_is_used_at_least_once():
    text = "\n".join(BODIES.values())
    for name in CHAT_PLACEHOLDERS:
        assert f"${name}" in text, name


def test_separator_width_is_fixed_for_the_whole_prompt():
    assert SEPARATOR_WIDTH == 30


def topic_context() -> TopicContext:
    return TopicContext(
        persona_section="人设正文",
        capabilities="- 能力",
        resources="- 资源",
        sentence_max_chars="40",
        sentence_max_width="80",
        persona_hint_chars="20",
    )


def test_every_topic_renders_a_body_without_its_own_heading():
    context = topic_context()
    for topic in TOPIC_REGISTRY:
        body = topic.render(context)
        assert body.strip()
        assert "【" not in body
        assert "】" not in body


def test_topics_substitute_their_placeholders_from_the_context():
    context = topic_context()
    capabilities_body = "下面是你可以交办的事项；派发时按任务内容描述，不必指定名称。\n- 能力"
    assert TOPIC_PERSONA.render(context) == "人设正文"
    assert TOPIC_CAPABILITIES.render(context) == capabilities_body
    assert TOPIC_RESOURCES.render(context) == "- 资源"
    assert "不超过 40 个汉字" in TOPIC_SPEECH_RULES.render(context)
    assert "80 个英文字符" in TOPIC_SPEECH_RULES.render(context)
    assert "约 20 字" in TOPIC_SPEECH_RULES.render(context)
