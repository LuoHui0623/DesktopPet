import math
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import BaseMessage

MESSAGE_OVERHEAD = 4


class TokenCountError(Exception):
    pass


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    estimated: bool = False


class TokenCounter(Protocol):
    def count(self, messages: Any) -> TokenCount: ...


class TiktokenCounter:
    """按真实分词器计数；构造时不加载词表，编码不可用时在计数时报错。"""

    def __init__(self, encoding: str) -> None:
        self._encoding_name = encoding
        self._encoding: Any = None

    def count(self, messages: Any) -> TokenCount:
        encoding = self._load()
        total = 0
        for message in _messages(messages):
            total += len(encoding.encode(_text(message), disallowed_special=())) + MESSAGE_OVERHEAD
        return TokenCount(total)

    def _load(self) -> Any:
        if self._encoding is None:
            try:
                import tiktoken
            except ImportError as exc:
                raise TokenCountError("tiktoken 未安装") from exc
            try:
                self._encoding = tiktoken.get_encoding(self._encoding_name)
            except (ValueError, KeyError) as exc:
                raise TokenCountError(f"未知分词器 {self._encoding_name!r}") from exc
        return self._encoding


class HeuristicCounter:
    """本地估算：中日韩字符按 1 token，其余字符按 4 字符 1 token。"""

    def count(self, messages: Any) -> TokenCount:
        total = 0
        for message in _messages(messages):
            total += _estimate(_text(message)) + MESSAGE_OVERHEAD
        return TokenCount(total, estimated=True)


class FallbackCounter:
    def __init__(self, primary: TokenCounter, fallback: TokenCounter) -> None:
        self._primary = primary
        self._fallback = fallback

    def count(self, messages: Any) -> TokenCount:
        try:
            return self._primary.count(messages)
        except TokenCountError:
            return self._fallback.count(messages)


def create_counter(tokenizer: str) -> TokenCounter:
    if tokenizer == "estimate":
        return HeuristicCounter()
    return FallbackCounter(TiktokenCounter(tokenizer), HeuristicCounter())


def _messages(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _text(message: Any) -> str:
    if isinstance(message, BaseMessage):
        return _content(message.content)
    if isinstance(message, dict):
        return _content(message.get("content"))
    if isinstance(message, str):
        return message
    return str(message)


def _content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def _estimate(text: str) -> int:
    wide = sum(1 for char in text if _is_wide(char))
    narrow = len(text) - wide
    return wide + math.ceil(narrow / 4)


def _is_wide(char: str) -> bool:
    code = ord(char)
    return 0x3000 <= code <= 0x9FFF or 0xAC00 <= code <= 0xD7AF or 0xFF00 <= code <= 0xFFEF
