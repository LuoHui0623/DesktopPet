"""AI 输出解析 helper：markdown fence 剥离、JSON 提取与解析、模型文本清洗、DSML 统一出口。

模型输出不是可信输入：可能把 JSON 包进代码块或夹在说明文字里，可能泄漏 DSML
私有标签、零宽字符或控制字符。本模块集中这类解析与清洗，供输出协议
（`chat_output`）、子代理循环（`harness/loop`）与调用方共用；各处不再自带
正则副本，避免规则漂移。

`parse_dsml` / `DsmlError` / `DsmlCall` 在此重导出作为统一出口，实现与语义
仍归 `dsml.py` 所有；`agent/spec/plan.py` 受分层守护约束不依赖本模块，
其 fence 逻辑保持自包含，属有意为之的例外。
"""

import json
import re
from typing import Any

from agent.prompts.dsml import DsmlCall, DsmlError, parse_dsml

__all__ = [
    "DsmlCall",
    "DsmlError",
    "clean_output_text",
    "extract_json_object",
    "parse_dsml",
    "parse_json_output",
    "strip_code_fence",
]

FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")
JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
PRIVATE_TAG = re.compile(
    r"<[^<>\n]{0,200}DSML[^<>\n]{0,200}>"
    r"|<[|｜][^<>\n]{0,200}[|｜]>"
)
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_code_fence(text: str) -> str:
    """剥离首尾 markdown 代码围栏（含 ```` ```json ```` 语言标记），其余内容原样保留。"""
    return FENCE.sub("", text.strip()).strip()


def extract_json_object(text: str) -> str:
    """从模型回复中取最外层 JSON 对象文本；模型偶尔把 JSON 包进代码块或夹在说明文字里。

    先剥围栏，再取首个 `{` 到末个 `}` 的整段；找不到对象时返回剥围栏后的原文，
    交由调用方解析后自行报错。
    """
    stripped = strip_code_fence(text)
    match = JSON_OBJECT.search(stripped)
    return match.group(0) if match else stripped


def parse_json_output(text: str) -> Any:
    """剥离围栏并提取 JSON 对象后反序列化；非法输入按 ``json.JSONDecodeError`` 抛出。"""
    return json.loads(extract_json_object(text))


def clean_output_text(text: str) -> str:
    """清洗模型输出文本：去掉泄漏的 DSML/私有标签、零宽字符与控制字符。"""
    cleaned = PRIVATE_TAG.sub("", text)
    cleaned = ZERO_WIDTH.sub("", cleaned)
    return CONTROL.sub("", cleaned)
