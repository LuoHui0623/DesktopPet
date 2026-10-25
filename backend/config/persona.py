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
class PersonaConfig:
    content: Mapping[str, Any]

    def to_normalized_json(self) -> str:
        return json.dumps(self.content, sort_keys=True, ensure_ascii=False)


def _join(location: str, key: str) -> str:
    return f"{location}.{key}" if location else key


def _normalize(value: Any, location: str, errors: list[str]) -> Any:
    if isinstance(value, str):
        if not value:
            errors.append(f"persona.yaml {location} 不能为空字符串")
        return value
    if isinstance(value, list):
        items: list[str] = []
        for index, item in enumerate(value):
            if isinstance(item, str) and item:
                items.append(item)
            else:
                errors.append(f"persona.yaml {location}[{index}] 必须是非空字符串")
        return tuple(sorted(set(items)))
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str) or not key:
                errors.append(f"persona.yaml {location} 的键必须是非空字符串，当前为 {key!r}")
                continue
            result[key] = _normalize(child, _join(location, key), errors)
        return result
    errors.append(f"persona.yaml {location} 类型不支持，只允许字符串、字符串列表与映射")
    return None


def load_persona(path: Path) -> PersonaConfig:
    if not path.exists():
        raise PersonaError([f"未找到 {path.name}"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PersonaError([f"persona.yaml 解析失败：{exc}"]) from exc
    if not isinstance(data, dict) or not data:
        raise PersonaError(["persona.yaml 顶层必须是非空映射"])
    errors: list[str] = []
    content = _normalize(data, "", errors)
    if errors:
        raise PersonaError(errors)
    return PersonaConfig(content=content)
