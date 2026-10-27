from pathlib import Path

import pytest

from config.persona import FieldsTheme, ListTheme, PersonaError, load_persona, parse_persona

PERSONA = """
role:
  名字: 小桌
  人设: 桌面陪伴助手，关心我的作息
personality:
  - 温和
  - 好奇
language_style:
  - 口语化
  - 少用套话
"""


def write_persona(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "persona.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_role_is_a_fields_theme_with_name_and_persona(tmp_path: Path):
    persona = load_persona(write_persona(tmp_path, PERSONA))
    role = persona.themes["role"]
    assert isinstance(role, FieldsTheme)
    assert role.values == {"名字": "小桌", "人设": "桌面陪伴助手，关心我的作息"}


def test_personality_and_language_style_are_list_themes(tmp_path: Path):
    persona = load_persona(write_persona(tmp_path, PERSONA))
    assert persona.themes["personality"] == ListTheme(items=("好奇", "温和"))
    assert persona.themes["language_style"] == ListTheme(items=("口语化", "少用套话"))


def test_role_must_have_name_and_persona(tmp_path: Path):
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, "role:\n  名字: 小桌\n"))
    assert any("缺少必填字段：人设" in error for error in exc.value.errors)


def test_role_name_and_persona_must_be_non_empty_strings(tmp_path: Path):
    text = "role:\n  名字: ''\n  人设: 陪伴\n"
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, text))
    assert any("role.名字" in error for error in exc.value.errors)


def test_role_may_carry_extra_fields(tmp_path: Path):
    text = PERSONA.replace("role:\n", "role:\n  口头禅: 嗯哼\n  年龄: 三岁\n")
    role = load_persona(write_persona(tmp_path, text)).themes["role"]
    assert role.values["口头禅"] == "嗯哼"
    assert role.values["年龄"] == "三岁"
    assert role.values["名字"] == "小桌"


def test_role_extra_fields_must_be_strings(tmp_path: Path):
    text = PERSONA.replace("role:\n", "role:\n  标签:\n    - 一\n")
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, text))
    assert any("role.标签" in error for error in exc.value.errors)


def test_role_must_be_a_mapping(tmp_path: Path):
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, "role:\n  - 小桌\n"))
    assert any("字段映射" in error for error in exc.value.errors)


def test_unknown_theme_is_rejected_with_allowed_list(tmp_path: Path):
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, PERSONA + "mood:\n  - 开心\n"))
    message = str(exc.value)
    assert "mood" in message
    assert "role、personality、language_style" in message


def test_role_is_required(tmp_path: Path):
    text = "personality:\n  - 温和\n"
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, text))
    assert any("缺少必填主题：role" in error for error in exc.value.errors)


def test_list_themes_are_optional(tmp_path: Path):
    persona = load_persona(write_persona(tmp_path, "role:\n  名字: 小桌\n  人设: 陪伴\n"))
    assert set(persona.themes) == {"role"}


def test_list_theme_must_be_a_list(tmp_path: Path):
    text = "role:\n  名字: 小桌\n  人设: 陪伴\npersonality: 温和\n"
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, text))
    assert any("personality 必须是字符串列表" in error for error in exc.value.errors)


def test_list_items_must_be_non_empty_strings(tmp_path: Path):
    text = PERSONA.replace("- 少用套话", "- 1")
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, text.replace("- 1", "- ''")))
    assert any("language_style[1]" in error for error in exc.value.errors)


def test_empty_list_theme_is_rejected(tmp_path: Path):
    text = PERSONA.replace("personality:\n  - 温和\n  - 好奇\n", "personality: []\n")
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, text))
    assert any("不能为空列表" in error for error in exc.value.errors)


def test_key_order_does_not_change_normalized_json(tmp_path: Path):
    reordered = """
language_style:
  - 少用套话
  - 口语化
role:
  人设: 桌面陪伴助手，关心我的作息
  名字: 小桌
personality:
  - 好奇
  - 温和
"""
    original = load_persona(write_persona(tmp_path, PERSONA)).to_normalized_json()
    shuffled = load_persona(write_persona(tmp_path, reordered)).to_normalized_json()
    assert original == shuffled


def test_duplicate_list_items_are_removed(tmp_path: Path):
    text = PERSONA.replace("- 少用套话", "- 口语化")
    persona = load_persona(write_persona(tmp_path, text))
    assert persona.themes["language_style"] == ListTheme(items=("口语化",))


def test_normalized_json_keeps_unicode(tmp_path: Path):
    text = load_persona(write_persona(tmp_path, PERSONA)).to_normalized_json()
    assert "口语化" in text
    assert "\\u" not in text


def test_all_errors_are_reported_together(tmp_path: Path):
    text = """
role:
  名字: ""
  口头禅:
    - 嗯
personality:
  - 1
mood:
  - 开心
"""
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, text))
    errors = exc.value.errors
    assert any("role.名字" in error for error in errors)
    assert any("role.口头禅" in error for error in errors)
    assert any("personality[0]" in error for error in errors)
    assert any("mood" in error for error in errors)


def test_root_must_be_mapping(tmp_path: Path):
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, "- 口语化\n"))
    assert exc.value.errors


def test_empty_file_is_error(tmp_path: Path):
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, ""))
    assert exc.value.errors


def test_missing_file_is_error(tmp_path: Path):
    with pytest.raises(PersonaError) as exc:
        load_persona(tmp_path / "persona.yaml")
    assert any("未找到" in error for error in exc.value.errors)


def test_yaml_syntax_error_is_error(tmp_path: Path):
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, "role: [unclosed\n"))
    assert any("解析失败" in error for error in exc.value.errors)


def test_parse_persona_accepts_plain_mapping():
    persona = parse_persona({"role": {"名字": "小桌", "人设": "陪伴"}})
    assert persona.themes["role"] == FieldsTheme(values={"名字": "小桌", "人设": "陪伴"})
