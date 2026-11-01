import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig

from infra.llm.tokens import TokenCount, TokenCounter, create_counter

logger = logging.getLogger("pet")


@dataclass(frozen=True)
class UsageSample:
    provider: str
    model: str
    purpose: str | None
    run_id: str | None
    tokens_in: int | None
    tokens_out: int | None
    usage_raw: str | None
    started_at: str
    finished_at: str
    latency_ms: int
    success: bool
    prompt_hash: str | None = None
    prompt_text: str | None = None
    reply_text: str | None = None
    turn_id: str | None = None
    reply_raw: str | None = None


@dataclass(frozen=True)
class CallStart:
    """调用发起时的快照：此时还没有回复，token 与耗时均未知。"""

    provider: str
    model: str
    purpose: str | None
    turn_id: str | None
    run_id: str | None
    prompt_hash: str | None
    started_at: str
    prompt_text: str | None


class UsageObserver(Protocol):
    def call_started(self, event: CallStart) -> None: ...

    def call_finished(self, sample: UsageSample) -> None: ...


UsageSink = Callable[[UsageSample], None]


@dataclass(frozen=True)
class UsageScope:
    purpose: str
    run_id: str | None = None
    prompt_hash: str | None = None
    turn_id: str | None = None


_scope: ContextVar[UsageScope | None] = ContextVar("llm_call_scope", default=None)


@contextmanager
def usage_scope(
    purpose: str,
    run_id: str | None = None,
    prompt_hash: str | None = None,
    turn_id: str | None = None,
) -> Iterator[None]:
    token = _scope.set(
        UsageScope(purpose=purpose, run_id=run_id, prompt_hash=prompt_hash, turn_id=turn_id)
    )
    try:
        yield
    finally:
        _scope.reset(token)


def discard_usage(sample: UsageSample) -> None:
    return None


class TrackedChatModel:
    def __init__(
        self,
        model: BaseChatModel,
        *,
        provider: str,
        model_name: str,
        sink: UsageSink,
        context_window: int | None = None,
        max_tokens: int | None = None,
        tokenizer: str | None = None,
        counter: TokenCounter | None = None,
        observer: UsageObserver | None = None,
    ) -> None:
        self._model = model
        self._provider = provider
        self._model_name = model_name
        self._sink = sink
        self._observer = observer
        self.context_window = context_window
        self.max_tokens = max_tokens
        self._counter = counter or create_counter(tokenizer or "estimate")

    def count_tokens(self, messages: Any) -> TokenCount:
        return self._counter.count(messages)

    def set_observer(self, observer: UsageObserver | None) -> None:
        self._observer = observer

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        started_at = _now()
        started = time.perf_counter()
        scope = _scope.get()
        prompt = _prompt_text(input)
        self._notify_started(scope, started_at, prompt)
        try:
            response = self._model.invoke(input, config, **kwargs)
        except Exception:
            self._record(scope, started_at, started, None, success=False, prompt=prompt)
            raise
        self._record(scope, started_at, started, response, success=True, prompt=prompt)
        return response

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        started_at = _now()
        started = time.perf_counter()
        scope = _scope.get()
        prompt = _prompt_text(input)
        self._notify_started(scope, started_at, prompt)
        try:
            response = await self._model.ainvoke(input, config, **kwargs)
        except Exception:
            self._record(scope, started_at, started, None, success=False, prompt=prompt)
            raise
        self._record(scope, started_at, started, response, success=True, prompt=prompt)
        return response

    def _record(
        self,
        scope: UsageScope | None,
        started_at: str,
        started: float,
        response: Any,
        *,
        success: bool,
        prompt: str | None,
    ) -> None:
        tokens_in, tokens_out, usage_raw = _usage(response)
        sample = UsageSample(
            provider=self._provider,
            model=self._model_name,
            purpose=scope.purpose if scope else None,
            run_id=scope.run_id if scope else None,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usage_raw=usage_raw,
            started_at=started_at,
            finished_at=_now(),
            latency_ms=round((time.perf_counter() - started) * 1000),
            success=success,
            prompt_hash=scope.prompt_hash if scope else None,
            prompt_text=prompt,
            reply_text=_content_text(getattr(response, "content", None)),
            turn_id=scope.turn_id if scope else None,
            reply_raw=_raw_reply(getattr(response, "content", None)),
        )
        try:
            self._sink(sample)
        except Exception:
            logger.exception("LLM 用量记录写入失败")
        if self._observer is not None:
            try:
                self._observer.call_finished(sample)
            except Exception:
                logger.exception("LLM 调用完成事件发布失败")

    def _notify_started(
        self, scope: UsageScope | None, started_at: str, prompt: str | None
    ) -> None:
        if self._observer is None:
            return
        try:
            self._observer.call_started(
                CallStart(
                    provider=self._provider,
                    model=self._model_name,
                    purpose=scope.purpose if scope else None,
                    turn_id=scope.turn_id if scope else None,
                    run_id=scope.run_id if scope else None,
                    prompt_hash=scope.prompt_hash if scope else None,
                    started_at=started_at,
                    prompt_text=prompt,
                )
            )
        except Exception:
            logger.exception("LLM 调用开始事件发布失败")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _usage(response: Any) -> tuple[int | None, int | None, str | None]:
    if response is None:
        return None, None, None
    metadata = getattr(response, "usage_metadata", None) or {}
    response_metadata = getattr(response, "response_metadata", None) or {}
    raw = response_metadata.get("token_usage") or response_metadata.get("usage") or metadata
    usage_raw = json.dumps(raw, ensure_ascii=False, default=str) if raw else None
    return _token(metadata.get("input_tokens")), _token(metadata.get("output_tokens")), usage_raw


def _token(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _prompt_text(value: Any) -> str | None:
    """完整记录本次输入：只有系统提示加段落标记，历史与输入本身已带角色与时间前缀。"""
    messages = _messages(value)
    if not messages:
        return _message_text(value)
    blocks = []
    for message in messages:
        text = _content_text(message.content)
        if not text:
            continue
        blocks.append(f"[system]\n{text}" if message.type == "system" else text)
    return "\n\n".join(blocks) or None


def _messages(value: Any) -> list[BaseMessage]:
    if isinstance(value, BaseMessage):
        return [value]
    if isinstance(value, (list, tuple)) and value:
        if all(isinstance(item, BaseMessage) for item in value):
            return list(value)
        return []
    converter = getattr(value, "to_messages", None)
    if callable(converter):
        try:
            converted = converter()
        except Exception:
            return []
        if isinstance(converted, (list, tuple)) and all(
            isinstance(item, BaseMessage) for item in converted
        ):
            return list(converted)
    return []


def _message_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, BaseMessage):
        return _content_text(value.content)
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return _message_text(value[1])
    if isinstance(value, dict):
        return _content_text(value.get("content"))
    if isinstance(value, (list, tuple)):
        parts = [_message_text(item) for item in value]
        return "\n".join(part for part in parts if part) or None
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    return str(value)


def _content_text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts) or None
    return str(content)


def _raw_reply(content: Any) -> str | None:
    """原始响应：字符串原样保留，结构化内容整体序列化，不做文本归一化。"""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)
