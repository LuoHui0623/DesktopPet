from hashlib import sha256
from pathlib import Path

import pytest

from config.persona import parse_persona
from constants.path import PROMPTS_DIR
from module.agent.chat_system_prompt import (
    ACTION_RULES_BODY,
    CONSTRAINTS_BODY,
    TOPIC_BODIES,
    TOPIC_ORDER,
    TOPIC_PERSONA,
)
from module.agent.prompt import (
    NO_CAPABILITIES,
    NO_RESOURCES,
    CapabilityDescription,
    PromptError,
    ResourceItem,
    build_system_prompt,
    compose_chat_prompt,
    default_resources,
    load_capabilities,
    load_templates,
    merge_capabilities,
    read_template,
    render_persona,
)

PERSONA = parse_persona(
    {
        "role": {"名字": "小桌", "人设": "桌面陪伴助手", "口头禅": "嗯哼"},
        "personality": ["好奇", "温和"],
        "language_style": ["口语化"],
    }
)

CAPABILITIES = [
    CapabilityDescription(name="打开网址", description="打开白名单内的网址"),
    CapabilityDescription(name="时间与天气", description="查询时间和天气"),
]

RESOURCES = [
    ResourceItem(label="草稿本", resource_id="draft"),
    ResourceItem(label="结构化记忆", resource_id="structured"),
]


@pytest.fixture
def templates():
    return load_templates()


def build(templates, **overrides):
    kwargs = {"capabilities": CAPABILITIES, "resources": RESOURCES}
    kwargs.update(overrides)
    return build_system_prompt("chat", PERSONA, templates, **kwargs)


def composed() -> str:
    return compose_chat_prompt(order=TOPIC_ORDER, bodies=TOPIC_BODIES)


def test_role_is_filled_into_its_sentence_and_extras_follow():
    text = render_persona(PERSONA)
    assert text.startswith("你的名字是小桌。桌面陪伴助手\n口头禅：嗯哼")


def test_list_themes_are_rendered_with_heading_and_sorted_bullets():
    text = render_persona(PERSONA)
    assert "性格：\n- 好奇\n- 温和" in text
    assert "说话风格：\n- 口语化" in text


def test_themes_are_rendered_in_fixed_order():
    text = render_persona(PERSONA)
    assert text.index("你的名字是") < text.index("性格：") < text.index("说话风格：")


def test_missing_optional_theme_has_no_block():
    persona = parse_persona({"role": {"名字": "小桌", "人设": "陪伴"}})
    assert render_persona(persona) == "你的名字是小桌。陪伴"


def test_composition_follows_topic_order_with_blank_lines_between_blocks():
    expected = "\n\n".join(f"【{topic}】\n{TOPIC_BODIES[topic]}" for topic in TOPIC_ORDER)
    assert composed() == expected


def test_sections_follow_fixed_order(templates):
    text = build(templates).text
    positions = [text.index(f"【{topic}】") for topic in TOPIC_ORDER]
    assert positions == sorted(positions)


def test_sections_keep_their_body_verbatim(templates):
    text = build(templates).text
    assert f"【行动规则】\n{ACTION_RULES_BODY}" in text
    assert f"【约束】\n{CONSTRAINTS_BODY}" in text


def test_action_rules_describe_the_chat_output_format(templates):
    text = build(templates).text
    rules = text[text.index("【行动规则】") : text.index("【可用能力】")]
    assert '{"type": "chat", "text": ["要说给主人的话"]}' in rules
    assert "最多说四句话" in rules
    assert '"type" 必须是第一个字段' in text


def test_action_rules_group_details_under_chat_and_dispatch(templates):
    text = build(templates).text
    rules = text[text.index("【行动规则】") : text.index("【可用能力】")]
    chat_at = rules.index("一、对话")
    dispatch_at = rules.index("二、派发任务")
    assert chat_at < dispatch_at
    assert rules.index('"text"') < dispatch_at
    assert dispatch_at < rules.index('"plan"')


def test_capabilities_note_is_its_own_line_under_the_heading(templates):
    text = build(templates).text
    section = text[text.index("【可用能力】") : text.index("【可用资源】")]
    lines = section.splitlines()
    assert lines[1] == "只提供能力名，具体做法由执行者自行查阅说明书。"
    assert lines[2] == "- 打开网址: 打开白名单内的网址"


def test_capabilities_are_listed_inside_their_own_section(templates):
    text = build(templates).text
    assert text.index("【可用能力】") < text.index("- 打开网址") < text.index("【可用资源】")


def test_capabilities_file_is_read_from_dash_lines(tmp_path: Path):
    path = tmp_path / "capabilities.md"
    path.write_text(
        "# 可用能力\n\n说明文字。\n- 记忆：记录主人告诉你的稳定信息\n- 天气：查询天气\n",
        encoding="utf-8",
    )
    assert load_capabilities(path) == (
        CapabilityDescription(name="记忆", description="记录主人告诉你的稳定信息"),
        CapabilityDescription(name="天气", description="查询天气"),
    )


def test_missing_capabilities_file_means_none(tmp_path: Path):
    assert load_capabilities(tmp_path / "missing.md") == ()


def test_malformed_capability_line_is_reported_with_line_number(tmp_path: Path):
    path = tmp_path / "capabilities.md"
    path.write_text("- 记忆：好\n- 没有分隔符\n", encoding="utf-8")
    with pytest.raises(PromptError, match="第 2 行"):
        load_capabilities(path)


def test_real_capabilities_file_loads():
    assert load_capabilities() == ()


def test_merge_capabilities_keeps_file_entries_and_appends_registry_ones():
    merged = merge_capabilities(
        (CapabilityDescription(name="记忆写入", description="来自文件"),),
        (
            CapabilityDescription(name="记忆写入", description="来自注册表"),
            CapabilityDescription(name="打开网址", description="打开白名单内的网址"),
        ),
    )
    assert merged == (
        CapabilityDescription(name="记忆写入", description="来自文件"),
        CapabilityDescription(name="打开网址", description="打开白名单内的网址"),
    )


def test_merge_capabilities_of_nothing_is_empty():
    assert merge_capabilities() == ()
    assert merge_capabilities((), ()) == ()


def test_capabilities_are_listed_one_per_line(templates):
    text = build(templates).text
    assert "- 打开网址: 打开白名单内的网址\n- 时间与天气: 查询时间和天气" in text


def test_resources_are_listed_one_per_line(templates):
    text = build(templates).text
    assert "- 草稿本: draft\n- 结构化记忆: structured" in text


def test_default_resources_are_registered_ids():
    items = default_resources()
    assert [(item.label, item.resource_id) for item in items] == [
        ("草稿本", "draft"),
        ("结构化记忆", "structured"),
        ("永久记忆", "permanent"),
        ("小世界", "world"),
    ]


def test_empty_sections_use_placeholders(templates):
    text = build(templates, capabilities=(), resources=()).text
    assert NO_CAPABILITIES in text
    assert NO_RESOURCES in text


def test_speaking_rules_come_from_constants(templates):
    text = build(templates).text
    assert "每条发言不超过 40 个汉字或 80 个英文字符" in text
    assert "人设提示约 20 字" in text


def test_speaking_rules_scope_the_limit_to_each_text_item(templates):
    text = build(templates).text
    rules = text[text.index("【发言规则】") : text.index("【约束】")]
    assert "每一个 str" in rules
    assert "整个 output" in rules


def test_every_placeholder_is_substituted(templates):
    text = build(templates).text
    assert "$" not in text


def test_same_input_renders_same_prompt(templates):
    first = build(templates)
    second = build(templates)
    assert first == second


def test_persona_values_are_not_interpolated_again(templates):
    persona = parse_persona({"role": {"名字": "$capabilities", "人设": "$resources"}})
    text = build_system_prompt("chat", persona, templates, capabilities=CAPABILITIES).text
    assert "你的名字是$capabilities。$resources" in text
    assert "- 打开网址" in text


def test_prompt_carries_template_digest(templates):
    result = build(templates)
    assert result.template_digest == templates.digest
    assert len(result.template_digest) == 64


def test_digest_hashes_the_composed_prompt(templates):
    assert templates.digest == sha256(composed().encode("utf-8")).hexdigest()


def test_digest_changes_when_a_body_changes(monkeypatch):
    before = load_templates().digest
    bodies = {**TOPIC_BODIES, TOPIC_PERSONA: "$persona_section 补一句。"}
    monkeypatch.setattr("module.agent.prompt.TOPIC_BODIES", bodies)
    assert load_templates().digest != before


def test_composition_rejects_a_topic_without_body():
    with pytest.raises(PromptError, match="缺少正文"):
        compose_chat_prompt(order=(TOPIC_PERSONA, "不存在"), bodies={TOPIC_PERSONA: "正文"})


def test_composition_rejects_an_empty_body():
    with pytest.raises(PromptError, match="正文为空"):
        compose_chat_prompt(order=(TOPIC_PERSONA,), bodies={TOPIC_PERSONA: " \n "})


def test_composition_rejects_placeholder_mismatch():
    with pytest.raises(PromptError, match="占位符"):
        compose_chat_prompt(
            order=(TOPIC_PERSONA,), bodies={TOPIC_PERSONA: "$persona_section $extra"}
        )


def test_unknown_role_is_error(templates):
    with pytest.raises(PromptError):
        build_system_prompt("unknown", PERSONA, templates)  # type: ignore[arg-type]


def test_read_template_still_reads_files_for_subagent(tmp_path: Path):
    (tmp_path / "subagent.md").write_text("$task", encoding="utf-8")
    assert read_template(tmp_path, "subagent.md", frozenset({"task"})) == "$task"


def test_read_template_reports_missing_file_with_its_name(tmp_path: Path):
    with pytest.raises(PromptError) as exc:
        read_template(tmp_path, "missing.md", frozenset())
    assert "missing.md" in str(exc.value)


def test_read_template_reports_placeholder_mismatch(tmp_path: Path):
    (tmp_path / "subagent.md").write_text("$task $extra", encoding="utf-8")
    with pytest.raises(PromptError) as exc:
        read_template(tmp_path, "subagent.md", frozenset({"task"}))
    message = str(exc.value)
    assert "subagent.md" in message
    assert "extra" in message


def test_chat_prompt_files_are_gone():
    assert not (PROMPTS_DIR / "chat.md").exists()
    assert not (PROMPTS_DIR / "persona.md").exists()
