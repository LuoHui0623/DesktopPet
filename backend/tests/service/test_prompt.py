from pathlib import Path

import pytest

from config.persona import PersonaConfig
from service.agent import prompt
from service.agent.prompt import (
    NO_CAPABILITIES,
    CapabilityDescription,
    PromptError,
    build_system_prompt,
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


def test_persona_is_rendered_as_nested_lines():
    text = render_persona(PERSONA)
    assert "role:" in text
    assert "  name: 小桌" in text
    assert "  personality:\n    - 好奇\n    - 温和" in text
    assert "language_style:\n  - 口语化" in text


def test_persona_comes_before_capabilities():
    prompt_text = build_system_prompt("chat", PERSONA, CAPABILITIES)
    assert prompt_text.index("【人设】") < prompt_text.index("【可用能力】")
    assert prompt_text.index("  name: 小桌") < prompt_text.index("【可用能力】")


def test_capabilities_are_listed_one_per_line():
    prompt_text = build_system_prompt("chat", PERSONA, CAPABILITIES)
    assert "- 打开网址: 打开白名单内的网址\n- 时间与天气: 查询时间和天气" in prompt_text


def test_empty_capabilities_use_placeholder():
    prompt_text = build_system_prompt("chat", PERSONA)
    assert prompt_text.endswith(NO_CAPABILITIES + "\n")


def test_same_input_renders_same_prompt():
    first = build_system_prompt("chat", PERSONA, CAPABILITIES)
    second = build_system_prompt("chat", PERSONA, CAPABILITIES)
    assert first == second


def test_persona_values_are_not_interpolated_again():
    persona = PersonaConfig(content={"role": {"name": "$capabilities"}})
    prompt_text = build_system_prompt("chat", persona, CAPABILITIES)
    assert "name: $capabilities" in prompt_text
    assert "- 打开网址" in prompt_text


def test_unknown_role_is_error():
    with pytest.raises(PromptError):
        build_system_prompt("unknown", PERSONA)  # type: ignore[arg-type]


def test_placeholder_mismatch_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "persona.md").write_text("$persona $extra", encoding="utf-8")
    (tmp_path / "chat.md").write_text("$persona_section $capabilities", encoding="utf-8")
    monkeypatch.setattr(prompt, "PROMPTS_DIR", tmp_path)
    with pytest.raises(PromptError) as exc:
        build_system_prompt("chat", PERSONA)
    assert "persona.md" in str(exc.value)


def test_missing_template_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(prompt, "PROMPTS_DIR", tmp_path)
    with pytest.raises(PromptError) as exc:
        build_system_prompt("chat", PERSONA)
    assert "persona.md" in str(exc.value)
