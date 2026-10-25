from pathlib import Path

import pytest

from config.persona import PersonaError, load_persona

PERSONA = """
role:
  name: 小桌
  identity: 桌面陪伴助手
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


def test_parses_nested_mapping_and_lists(tmp_path: Path):
    content = load_persona(write_persona(tmp_path, PERSONA)).content
    assert content["role"]["name"] == "小桌"
    assert content["role"]["personality"] == ("好奇", "温和")
    assert content["language_style"] == ("口语化", "少用套话")


def test_accepts_arbitrary_field_names(tmp_path: Path):
    persona = load_persona(write_persona(tmp_path, PERSONA + "mood:\n  - 开心\n"))
    assert persona.content["mood"] == ("开心",)


def test_key_order_does_not_change_normalized_json(tmp_path: Path):
    reordered = """
language_style:
  - 少用套话
  - 口语化
role:
  personality:
    - 好奇
    - 温和
  identity: 桌面陪伴助手
  name: 小桌
"""
    original = load_persona(write_persona(tmp_path, PERSONA)).to_normalized_json()
    shuffled = load_persona(write_persona(tmp_path, reordered)).to_normalized_json()
    assert original == shuffled


def test_same_file_parses_to_same_normalized_json(tmp_path: Path):
    path = write_persona(tmp_path, PERSONA)
    assert load_persona(path).to_normalized_json() == load_persona(path).to_normalized_json()


def test_duplicate_list_items_are_removed(tmp_path: Path):
    text = PERSONA.replace("- 少用套话", "- 口语化")
    persona = load_persona(write_persona(tmp_path, text))
    assert persona.content["language_style"] == ("口语化",)


def test_normalized_json_keeps_unicode(tmp_path: Path):
    text = load_persona(write_persona(tmp_path, PERSONA)).to_normalized_json()
    assert "口语化" in text
    assert "\\u" not in text


def test_all_errors_are_reported_together(tmp_path: Path):
    text = """
role:
  name: ""
  age: 3
language_style:
  - 口语化
  - 1
"""
    with pytest.raises(PersonaError) as exc:
        load_persona(write_persona(tmp_path, text))
    errors = exc.value.errors
    assert len(errors) == 3
    assert any("role.name" in error for error in errors)
    assert any("role.age" in error for error in errors)
    assert any("language_style[1]" in error for error in errors)


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
