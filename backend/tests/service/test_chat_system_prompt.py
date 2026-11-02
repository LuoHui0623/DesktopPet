from string import Template

from module.agent import chat_system_prompt as module
from module.agent.chat_system_prompt import (
    ACTION_RULES_BODY,
    CAPABILITIES_BODY,
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
    TOPIC_ORDER,
    TOPIC_PERSONA,
    TOPIC_RESOURCES,
    TOPIC_SPEECH_RULES,
)

BODIES = {
    TOPIC_PERSONA: PERSONA_BODY,
    TOPIC_ACTION_RULES: ACTION_RULES_BODY,
    TOPIC_CAPABILITIES: CAPABILITIES_BODY,
    TOPIC_RESOURCES: RESOURCES_BODY,
    TOPIC_SPEECH_RULES: SPEECH_RULES_BODY,
    TOPIC_CONSTRAINTS: CONSTRAINTS_BODY,
}


def test_topic_names_are_distinct_and_readable():
    names = [
        TOPIC_PERSONA,
        TOPIC_ACTION_RULES,
        TOPIC_CAPABILITIES,
        TOPIC_RESOURCES,
        TOPIC_SPEECH_RULES,
        TOPIC_CONSTRAINTS,
    ]
    assert names == [
        "人设",
        "行动规则",
        "可用能力",
        "可用资源",
        "发言规则",
        "约束",
    ]
    assert len(set(names)) == len(names)


def test_order_is_the_only_source_of_sequence():
    assert TOPIC_ORDER == (
        TOPIC_PERSONA,
        TOPIC_ACTION_RULES,
        TOPIC_CAPABILITIES,
        TOPIC_RESOURCES,
        TOPIC_SPEECH_RULES,
        TOPIC_CONSTRAINTS,
    )


def test_order_covers_every_body_exactly_once():
    assert len(TOPIC_ORDER) == len(set(TOPIC_ORDER))
    assert set(TOPIC_ORDER) == set(BODIES)


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
    assert lines[0] == "只提供能力名，具体做法由执行者自行查阅说明书。"
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


def test_module_declares_no_behaviour():
    members = [
        name
        for name, value in vars(module).items()
        if not name.startswith("__") and (callable(value) or isinstance(value, type))
    ]
    assert members == []
