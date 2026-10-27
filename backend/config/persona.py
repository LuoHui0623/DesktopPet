import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class PersonaError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class FieldsSpec:
    required: tuple[str, ...]
    template: str


@dataclass(frozen=True)
class ListSpec:
    heading: str


THEME_SPECS: Mapping[str, FieldsSpec | ListSpec] = {
    "role": FieldsSpec(required=("名字", "人设"), template="你的名字是{名字}。{人设}"),
    "personality": ListSpec(heading="性格"),
    "language_style": ListSpec(heading="说话风格"),
}
REQUIRED_THEMES: tuple[str, ...] = ("role",)


@dataclass(frozen=True)
class FieldsTheme:
    values: Mapping[str, str]


@dataclass(frozen=True)
class ListTheme:
    items: tuple[str, ...]


Theme = FieldsTheme | ListTheme


@dataclass(frozen=True)
class PersonaConfig:
    themes: Mapping[str, Theme]

    def to_normalized_json(self) -> str:
        return json.dumps(_canonical(self.themes), sort_keys=True, ensure_ascii=False)


def _canonical(themes: Mapping[str, Theme]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, theme in themes.items():
        if isinstance(theme, FieldsTheme):
            result[key] = dict(sorted(theme.values.items()))
        else:
            result[key] = sorted(theme.items)
    return result


def _parse_fields(name: str, spec: FieldsSpec, value: Any, errors: list[str]) -> FieldsTheme | None:
    if not isinstance(value, dict):
        errors.append(f"persona.yaml {name} 必须是字段映射（包含 {'、'.join(spec.required)}）")
        return None
    values: dict[str, str] = {}
    for key, child in value.items():
        if not isinstance(key, str) or not key:
            errors.append(f"persona.yaml {name} 的字段名必须是非空字符串，当前为 {key!r}")
        elif not isinstance(child, str) or not child.strip():
            errors.append(f"persona.yaml {name}.{key} 必须是非空字符串")
        else:
            values[key] = child
    for key in spec.required:
        if key not in values:
            errors.append(f"persona.yaml {name} 缺少必填字段：{key}")
    if any(key not in values for key in spec.required):
        return None
    return FieldsTheme(values=values)


def _parse_list(name: str, value: Any, errors: list[str]) -> ListTheme | None:
    if not isinstance(value, list):
        errors.append(f"persona.yaml {name} 必须是字符串列表")
        return None
    items: list[str] = []
    valid = True
    for index, item in enumerate(value):
        if isinstance(item, str) and item.strip():
            items.append(item)
        else:
            valid = False
            errors.append(f"persona.yaml {name}[{index}] 必须是非空字符串")
    if not valid:
        return None
    if not items:
        errors.append(f"persona.yaml {name} 不能为空列表；不需要的主题请删除该键")
        return None
    return ListTheme(items=tuple(sorted(set(items))))


def parse_persona(data: Any) -> PersonaConfig:
    if not isinstance(data, dict) or not data:
        raise PersonaError(["persona.yaml 顶层必须是非空映射"])
    errors: list[str] = []
    for key in data:
        if key not in THEME_SPECS:
            allowed = "、".join(THEME_SPECS)
            errors.append(f"persona.yaml 含未知主题 {key!r}，可用主题为：{allowed}")
    for key in REQUIRED_THEMES:
        if key not in data:
            errors.append(f"persona.yaml 缺少必填主题：{key}")
    themes: dict[str, Theme] = {}
    for key, value in data.items():
        spec = THEME_SPECS.get(key)
        if spec is None:
            continue
        if isinstance(spec, FieldsSpec):
            theme = _parse_fields(key, spec, value, errors)
        else:
            theme = _parse_list(key, value, errors)
        if theme is not None:
            themes[key] = theme
    if errors:
        raise PersonaError(errors)
    return PersonaConfig(themes=themes)


def load_persona(path: Path) -> PersonaConfig:
    if not path.exists():
        raise PersonaError([f"未找到 {path.name}"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PersonaError([f"persona.yaml 解析失败：{exc}"]) from exc
    return parse_persona(data)
