import shutil
from pathlib import Path

import pytest

from config.persona import parse_persona
from constants.path import PROMPTS_DIR
from module.agent.prompt import (
    NO_CAPABILITIES,
    NO_RESOURCES,
    CapabilityDescription,
    PromptError,
    ResourceItem,
    build_system_prompt,
    default_resources,
    load_capabilities,
    load_templates,
    merge_capabilities,
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
    ResourceItem(label="草稿本", path="data/resources/草稿本.md"),
    ResourceItem(label="表情包", path="data/resources/表情包"),
]


@pytest.fixture
def templates():
    return load_templates()


def build(templates, **overrides):
    kwargs = {"capabilities": CAPABILITIES, "resources": RESOURCES}
    kwargs.update(overrides)
    return build_system_prompt("chat", PERSONA, templates, **kwargs)


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


def test_sections_follow_fixed_order(templates):
    text = build(templates).text
    positions = [
        text.index("你的名字是小桌"),
        text.index("【行动规则】"),
        text.index("【可用能力】"),
        text.index("【可用资源】"),
        text.index("【发言规则】"),
        text.index("【固定约束】"),
    ]
    assert positions == sorted(positions)


def test_action_rules_describe_the_chat_output_format(templates):
    text = build(templates).text
    rules = text[text.index("【行动规则】") : text.index("【可用能力】")]
    assert '{"type": "chat", "text": "要说给主人的话"}' in rules
    assert "type 必须是第一个字段" in rules


def test_capabilities_are_listed_inside_action_rules(templates):
    text = build(templates).text
    assert text.index("【行动规则】") < text.index("- 打开网址") < text.index("【可用资源】")


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
    assert "- 草稿本: data/resources/草稿本.md\n- 表情包: data/resources/表情包" in text


def test_default_resources_point_into_resource_dir():
    labels = [item.label for item in default_resources()]
    assert labels == ["草稿本", "表情包"]
    assert all(item.path.startswith("backend/data/resources/") for item in default_resources())


def test_empty_sections_use_placeholders(templates):
    text = build(templates, capabilities=(), resources=()).text
    assert NO_CAPABILITIES in text
    assert NO_RESOURCES in text


def test_speaking_rules_come_from_constants(templates):
    text = build(templates).text
    assert "单句不超过 40 字符" in text
    assert "人设提示约 20 字" in text


def test_fixed_constraints_are_in_template(templates):
    text = build(templates).text
    assert "不编造记忆" in text


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


def test_digest_changes_when_template_changes(tmp_path: Path):
    shutil.copytree(PROMPTS_DIR, tmp_path / "prompts")
    directory = tmp_path / "prompts"
    before = load_templates(directory).digest
    (directory / "chat.md").write_text(
        (directory / "chat.md").read_text(encoding="utf-8") + "\n额外一行。\n",
        encoding="utf-8",
    )
    assert load_templates(directory).digest != before


def test_unknown_role_is_error(templates):
    with pytest.raises(PromptError):
        build_system_prompt("unknown", PERSONA, templates)  # type: ignore[arg-type]


def test_missing_template_fails_with_file_name(tmp_path: Path):
    shutil.copy(PROMPTS_DIR / "persona.md", tmp_path / "persona.md")
    with pytest.raises(PromptError) as exc:
        load_templates(tmp_path)
    assert "chat.md" in str(exc.value)


def test_placeholder_mismatch_fails_with_expected_and_actual(tmp_path: Path):
    shutil.copytree(PROMPTS_DIR, tmp_path / "prompts")
    directory = tmp_path / "prompts"
    (directory / "persona.md").write_text("$persona $extra", encoding="utf-8")
    with pytest.raises(PromptError) as exc:
        load_templates(directory)
    message = str(exc.value)
    assert "persona.md" in message
    assert "extra" in message


def test_real_templates_load():
    assert load_templates().digest
