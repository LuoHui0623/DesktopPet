import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from infra.store.db import connect
from infra.store.repositories import llm_usage
from infra.store.repositories.llm_usage import UsageRecord

logger = logging.getLogger("pet")

UsageSink = Callable[[UsageRecord], None]


@dataclass(frozen=True)
class UsageScope:
    purpose: str
    run_id: str | None = None
    prompt_hash: str | None = None


_scope: ContextVar[UsageScope | None] = ContextVar("llm_usage_scope", default=None)


@contextmanager
def usage_scope(
    purpose: str,
    run_id: str | None = None,
    prompt_hash: str | None = None,
) -> Iterator[None]:
    token = _scope.set(UsageScope(purpose=purpose, run_id=run_id, prompt_hash=prompt_hash))
    try:
        yield
    finally:
        _scope.reset(token)


def discard_usage(record: UsageRecord) -> None:
    return None


def db_usage_sink(db_file: Path) -> UsageSink:
    def write(record: UsageRecord) -> None:
        conn = connect(db_file)
        try:
            llm_usage.insert(conn, record)
        finally:
            conn.close()

    return write


class TrackedChatModel:
    def __init__(
        self,
        model: BaseChatModel,
        *,
        provider: str,
        model_name: str,
        sink: UsageSink,
    ) -> None:
        self._model = model
        self._provider = provider
        self._model_name = model_name
        self._sink = sink

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        started_at = _now()
        started = time.perf_counter()
        try:
            response = self._model.invoke(input, config, **kwargs)
        except Exception:
            self._record(started_at, started, None, success=False)
            raise
        self._record(started_at, started, response, success=True)
        return response

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        started_at = _now()
        started = time.perf_counter()
        try:
            response = await self._model.ainvoke(input, config, **kwargs)
        except Exception:
            self._record(started_at, started, None, success=False)
            raise
        self._record(started_at, started, response, success=True)
        return response

    def _record(self, started_at: str, started: float, response: Any, *, success: bool) -> None:
        tokens_in, tokens_out, usage_raw = _usage(response)
        scope = _scope.get()
        record = UsageRecord(
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
        )
        try:
            self._sink(record)
        except Exception:
            logger.exception("LLM 用量记录写入失败")


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
