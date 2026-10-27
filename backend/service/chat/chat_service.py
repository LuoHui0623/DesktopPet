import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from hashlib import sha256
from typing import Any, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import BaseMessage

from constants.speech import SENTENCE_MAX_CHARS
from infra.llm.usage import TrackedChatModel, UsageSink, usage_scope
from infra.store.repositories.daily_log import DailyLogRecord
from infra.store.repositories.llm_usage import UsageRecord
from service.agent.memory_capture import DEFAULT_NOTICE, MemoryCapture
from service.agent.prompt import PromptTemplates
from service.chat.citations import CitedSource, resolve_sources, split_citations
from service.chat.context import ContextBuilder, MemoryFragment
from service.llm import LlmService, LlmUnavailableError
from service.memory.core_memory import CoreMemoryService
from service.memory.daily_log_service import DailyLogService
from service.memory.memory_service import StructuredMemoryService
from service.persona.persona_service import PersonaService, PersonaSnapshot, PersonaUnavailableError

logger = logging.getLogger("pet")

SENTENCE = re.compile(r"[^。！？!?…\n]*[。！？!?…]+|[^。！？!?…\n]+|\n")
MAX_ATTEMPTS = 2
DEFAULT_TIMEZONE = ZoneInfo("Asia/Shanghai")

PetState = Literal["idle", "thinking", "speaking", "executing", "sleeping"]


class ChatError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ChatEvents(Protocol):
    def status(self, state: PetState) -> None: ...

    def chunk(self, message_id: str, text: str) -> None: ...

    def done(self, message_id: str, text: str, sources: Sequence[CitedSource]) -> None: ...


class NoChatEvents:
    def status(self, state: PetState) -> None:
        return None

    def chunk(self, message_id: str, text: str) -> None:
        return None

    def done(self, message_id: str, text: str, sources: Sequence[CitedSource]) -> None:
        return None


@dataclass(frozen=True)
class ChatReply:
    message_id: str
    text: str
    recorded: bool
    sources: tuple[CitedSource, ...] = ()


def split_sentences(text: str) -> list[str]:
    return [match.group().strip() for match in SENTENCE.finditer(text) if match.group().strip()]


def split_segments(text: str) -> list[str]:
    return [match.group() for match in SENTENCE.finditer(text) if match.group()]


def overlong_sentences(text: str) -> list[str]:
    return [sentence for sentence in split_sentences(text) if len(sentence) > SENTENCE_MAX_CHARS]


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _prompt_digest(template_digest: str, persona_id: str, persona_version: int) -> str:
    return sha256(f"{template_digest}|{persona_id}|{persona_version}".encode()).hexdigest()


class ChatService:
    def __init__(
        self,
        llm: LlmService,
        daily_log: DailyLogService,
        personas: PersonaService,
        templates: PromptTemplates,
        usage_sink: UsageSink,
        events: ChatEvents | None = None,
        *,
        core: CoreMemoryService | None = None,
        structured: StructuredMemoryService | None = None,
        timezone: tzinfo | None = None,
        clock: Callable[[tzinfo], datetime] | None = None,
        capture: MemoryCapture | None = None,
    ) -> None:
        self._llm = llm
        self._daily_log = daily_log
        self._usage_sink = usage_sink
        self._events = events or NoChatEvents()
        self._capture = capture
        self._personas = personas
        self._templates = templates
        self._core = core
        self._structured = structured
        self._timezone = timezone or DEFAULT_TIMEZONE
        self._clock = clock

    def reply(self, user_text: str, fragments: Sequence[MemoryFragment] = ()) -> ChatReply:
        text = user_text.strip()
        if not text:
            raise ChatError("empty_input", "消息不能为空")
        if self._capture is not None and self._capture.matches(text):
            return self._reply_from_capture(text)
        snapshot = self._current_persona()
        builder = ContextBuilder(
            snapshot.config,
            self._templates,
            self._daily_log,
            self._timezone,
            core=self._core,
            structured=self._structured,
            clock=self._clock,
        )
        assembled = builder.build(text, fragments)
        messages = assembled.messages
        digest = _prompt_digest(assembled.template_digest, snapshot.id, snapshot.version)
        try:
            client = self._llm.client_for("chat")
        except LlmUnavailableError as exc:
            self._record_unavailable(digest)
            raise ChatError("model_unavailable", str(exc)) from exc

        self._events.status("thinking")
        try:
            raw = self._generate(client, messages, digest)
            reply, cited = split_citations(raw)
            recorded = self._daily_log.record_turn(text, reply)
            message_id = uuid4().hex
            sources = tuple(resolve_sources(cited, assembled.injected))
            self._events.status("speaking")
            for segment in split_segments(reply):
                self._events.chunk(message_id, segment)
            self._events.done(message_id, reply, sources)
        finally:
            self._events.status("idle")
        return ChatReply(message_id=message_id, text=reply, recorded=recorded, sources=sources)

    def _current_persona(self) -> PersonaSnapshot:
        try:
            return self._personas.current()
        except PersonaUnavailableError as exc:
            raise ChatError(PersonaUnavailableError.code, str(exc)) from exc

    def _reply_from_capture(self, text: str) -> ChatReply:
        assert self._capture is not None
        self._events.status("executing")
        try:
            captured = self._capture.handle(text)
            notice = captured.notice or DEFAULT_NOTICE
            recorded = self._daily_log.record_turn(text, notice)
            message_id = uuid4().hex
            self._events.status("speaking")
            for segment in split_segments(notice):
                self._events.chunk(message_id, segment)
            self._events.done(message_id, notice, ())
        finally:
            self._events.status("idle")
        return ChatReply(message_id=message_id, text=notice, recorded=recorded)

    def history(
        self,
        *,
        keyword: str | None,
        date_from: str | None,
        date_to: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DailyLogRecord], bool]:
        records = self._daily_log.search(
            keyword,
            date_from,
            date_to,
            newest_first=True,
            limit=limit + 1,
            offset=offset,
        )
        return records[:limit], len(records) > limit

    def _generate(
        self,
        client: TrackedChatModel,
        messages: list[BaseMessage],
        digest: str,
    ) -> str:
        for _ in range(MAX_ATTEMPTS):
            content = self._invoke(client, messages, digest)
            body, _ = split_citations(content)
            if not overlong_sentences(body):
                return content
        raise ChatError(
            "sentence_too_long",
            f"回复中有单句超过 {SENTENCE_MAX_CHARS} 字符，重新生成后仍超限",
        )

    def _invoke(self, client: TrackedChatModel, messages: list[BaseMessage], digest: str) -> str:
        with usage_scope("chat", prompt_hash=digest):
            try:
                response = client.invoke(messages)
            except Exception as exc:
                raise ChatError("model_failed", f"模型调用失败：{exc}") from exc
        content = _text_of(response.content).strip()
        body, _ = split_citations(content)
        if not body:
            raise ChatError("model_failed", "模型返回了空回复")
        return content

    def _record_unavailable(self, digest: str) -> None:
        active = self._llm.active_model("chat")
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        record = UsageRecord(
            provider=active.provider if active else "-",
            model=active.model if active else "-",
            purpose="chat",
            run_id=None,
            tokens_in=None,
            tokens_out=None,
            usage_raw=None,
            started_at=now,
            finished_at=now,
            latency_ms=0,
            success=False,
            prompt_hash=digest,
        )
        try:
            self._usage_sink(record)
        except Exception:
            logger.exception("LLM 用量记录写入失败")
