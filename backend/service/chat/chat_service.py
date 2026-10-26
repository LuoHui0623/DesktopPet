import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from config.persona import PersonaConfig
from constants.speech import SENTENCE_MAX_CHARS
from infra.llm.usage import TrackedChatModel, UsageSink, usage_scope
from infra.store.repositories.daily_log import DailyLogRecord
from infra.store.repositories.llm_usage import UsageRecord
from service.agent.prompt import PromptTemplates, build_system_prompt
from service.llm import LlmService, LlmUnavailableError
from service.memory.daily_log_service import DailyLogService

logger = logging.getLogger("pet")

SENTENCE = re.compile(r"[^。！？!?…\n]*[。！？!?…]+|[^。！？!?…\n]+|\n")
MAX_ATTEMPTS = 2

PetState = Literal["idle", "thinking", "speaking", "executing", "sleeping"]


class ChatError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ChatEvents(Protocol):
    def status(self, state: PetState) -> None: ...

    def chunk(self, message_id: str, text: str) -> None: ...

    def done(self, message_id: str, text: str) -> None: ...


class NoChatEvents:
    def status(self, state: PetState) -> None:
        return None

    def chunk(self, message_id: str, text: str) -> None:
        return None

    def done(self, message_id: str, text: str) -> None:
        return None


@dataclass(frozen=True)
class ChatReply:
    message_id: str
    text: str
    recorded: bool


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


class ChatService:
    def __init__(
        self,
        llm: LlmService,
        daily_log: DailyLogService,
        persona: PersonaConfig,
        templates: PromptTemplates,
        usage_sink: UsageSink,
        events: ChatEvents | None = None,
    ) -> None:
        self._llm = llm
        self._daily_log = daily_log
        self._persona = persona
        self._templates = templates
        self._usage_sink = usage_sink
        self._events = events or NoChatEvents()

    def reply(self, user_text: str) -> ChatReply:
        text = user_text.strip()
        if not text:
            raise ChatError("empty_input", "消息不能为空")
        prompt = build_system_prompt("chat", self._persona, self._templates)
        messages: list[BaseMessage] = [
            SystemMessage(content=prompt.text),
            HumanMessage(content=text),
        ]
        try:
            client = self._llm.client_for("chat")
        except LlmUnavailableError as exc:
            self._record_unavailable(prompt.template_digest)
            raise ChatError("model_unavailable", str(exc)) from exc

        self._events.status("thinking")
        try:
            reply = self._generate(client, messages, prompt.template_digest)
            recorded = self._daily_log.record_turn(text, reply)
            message_id = uuid4().hex
            self._events.status("speaking")
            for segment in split_segments(reply):
                self._events.chunk(message_id, segment)
            self._events.done(message_id, reply)
        finally:
            self._events.status("idle")
        return ChatReply(message_id=message_id, text=reply, recorded=recorded)

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
            if not overlong_sentences(content):
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
        if not content:
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
