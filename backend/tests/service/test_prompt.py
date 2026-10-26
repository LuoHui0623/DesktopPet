import shutil
from pathlib import Path

import pytest

from config.persona import PersonaConfig
from constants.path import PROMPTS_DIR
from service.agent.prompt import (
    NO_CAPABILITIES,
    NO_RESOURCES,
    CapabilityDescription,
    PromptError,
    ResourceItem,
    build_system_prompt,
    default_resources,
    load_templates,
    render_persona,
)

PERSONA = PersonaConfig(
    content={
        "role": {
            "name": "小桌",
            "identity": "桌面陪伴助手",
            "personality": ("好奇", "温和"),
        },
        "language_style": ("口语化",),
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


def test_persona_is_rendered_as_nested_lines():
    text = render_persona(PERSONA)
    assert "role:" in text
    assert "  name: 小桌" in text
    assert "  personality:\n    - 好奇\n    - 温和" in text
    assert "language_style:\n  - 口语化" in text


def test_sections_follow_fixed_order(templates):
    text = build(templates).text
    positions = [
        text.index("【人设】"),
        text.index("【可用能力】"),
        text.index("【可用资源】"),
        text.index("【发言规则】"),
        text.index("【固定约束】"),
    ]
    assert positions == sorted(positions)
    assert text.index("  name: 小桌") < text.index("【可用能力】")


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
    persona = PersonaConfig(content={"role": {"name": "$capabilities"}})
    text = build_system_prompt("chat", persona, templates, capabilities=CAPABILITIES).text
    assert "name: $capabilities" in text
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
