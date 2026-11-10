"""识别并解析模型泄漏的单次 DSML 调用，不直接执行任何动作。"""

import json
import re
from dataclasses import dataclass
from typing import Any

_TAG_START = re.compile(r"<\s*/?\s*[|｜]*DSML", re.IGNORECASE)
_TAG = re.compile(
    r"<(?P<close>/)?(?P<marker>[|｜]+DSML[|｜]+)\s+"
    r"(?P<name>[a-z]+)(?P<attrs>[^<>]*)>"
)
_ATTRIBUTE = re.compile(r'\s+([a-z]+)\s*=\s*"([^"]*)"')
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class DsmlError(ValueError):
    pass


@dataclass(frozen=True)
class DsmlCall:
    name: str
    parameters: dict[str, Any]


def _attributes(text: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    position = 0
    while position < len(text):
        if not text[position:].strip():
            break
        match = _ATTRIBUTE.match(text, position)
        if match is None or match[1] in attributes:
            raise DsmlError("标签属性不合法或重复")
        attributes[match[1]] = match[2]
        position = match.end()
    return attributes


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DsmlError("JSON 对象包含重复字段")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise DsmlError(f"JSON 常量不合法：{value}")


def _value(text: str, string: str) -> Any:
    if string == "true":
        return text
    if string != "false":
        raise DsmlError("参数 string 必须为 true 或 false")
    try:
        return json.loads(text, object_pairs_hook=_object, parse_constant=_invalid_constant)
    except DsmlError:
        raise
    except (ValueError, RecursionError) as exc:
        raise DsmlError("参数不是合法的 JSON") from exc


def parse_dsml(text: str) -> DsmlCall | None:
    """无 DSML 时返回 None；仅接受完整、无歧义的单个 calls/invoke。"""
    if _TAG_START.search(text) is None:
        return None

    text = text.strip()
    position = 0
    marker = ""

    def tag(name: str, *, closing: bool = False) -> dict[str, str]:
        nonlocal position, marker
        while position < len(text) and text[position].isspace():
            position += 1
        match = _TAG.match(text, position)
        if match is None or bool(match["close"]) != closing or match["name"] != name:
            raise DsmlError(f"缺少或不匹配的 {name} 标签")
        if not marker:
            marker = match["marker"]
        if match["marker"] != marker:
            raise DsmlError("DSML 标记不一致")
        position = match.end()
        attrs = _attributes(match["attrs"])
        if closing and attrs:
            raise DsmlError("结束标签不能带属性")
        return attrs

    if tag("calls"):
        raise DsmlError("calls 不能带属性")
    invocation = tag("invoke")
    if set(invocation) != {"name"} or not _IDENTIFIER.fullmatch(invocation["name"]):
        raise DsmlError("invoke 名称不合法")

    parameters: dict[str, Any] = {}
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        match = _TAG.match(text, position)
        if match is not None and match["close"] and match["name"] == "invoke":
            break
        attrs = tag("parameter")
        if (
            set(attrs) != {"name", "string"}
            or not _IDENTIFIER.fullmatch(attrs["name"])
            or attrs["name"] in parameters
        ):
            raise DsmlError("参数属性不合法或参数名重复")
        next_tag = _TAG_START.search(text, position)
        if next_tag is None:
            raise DsmlError("参数缺少结束标签")
        value = text[position : next_tag.start()]
        position = next_tag.start()
        tag("parameter", closing=True)
        parameters[attrs["name"]] = _value(value, attrs["string"])

    tag("invoke", closing=True)
    tag("calls", closing=True)
    if text[position:].strip():
        raise DsmlError("调用后存在多余内容")
    return DsmlCall(name=invocation["name"], parameters=parameters)
