import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from hashlib import sha256
from typing import Any, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from constants.speech import SENTENCE_MAX_CHARS
from infra.llm.usage import TrackedChatModel, UsageSample, UsageSink, usage_scope
from module.agent.approval import (
    ApprovalDraft,
    ApprovalGate,
    ApprovalOutcome,
    ApprovalRegistry,
    ApprovalTicket,
    AutoApprovalGate,
)
from module.agent.orchestrator import (
    STATUS_REJECTED,
    Checkpoint,
    Orchestrator,
    PlanError,
    PlanStep,
    checkpoint_block,
    parse_plan_data,
)
from module.agent.prompt import CapabilityDescription, PromptTemplates
from module.chat.activity import ChatActivity
from module.chat.citations import CitedSource, resolve_sources, split_citations
from module.chat.context import AssembledContext, ContextBuilder, MemoryFragment, PerceptionReader
from module.chat.transcript import ChatLine, ChatTranscript
from module.interaction.contracts import InteractionReader
from module.llm import LlmService, LlmUnavailableError
from module.memory.contracts import DailyLogView
from module.memory.daily_events import encode_event, event_text
from module.memory.daily_log_service import DailyLogService
from module.memory.memory_service import StructuredMemoryDoc
from module.persona.persona_service import PersonaService, PersonaSnapshot, PersonaUnavailableError

logger = logging.getLogger("pet")

SENTENCE = re.compile(r"[^。！？!?…\n]*[。！？!?…]+|[^。！？!?…\n]+|\n")
MAX_ATTEMPTS = 2
DEFAULT_TIMEZONE = ZoneInfo("Asia/Shanghai")
UNSPECIFIED_CAPABILITY = "（未指定）"
CHECKPOINT_INTRO = (
    "派发已执行完毕，下面是本次的检查点，每行一个 JSON，只供本轮使用：\n"
    "{blocks}\n"
    "现在只输出一条 chat 回复，把结果告诉主人。不要再派发，也不要复述 JSON。"
)

PetState = Literal["idle", "thinking", "speaking", "executing", "sleeping"]


class ChatError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ChatEvents(Protocol):
    def status(self, state: PetState) -> None: ...

    def chunk(self, message_id: str, text: str) -> None: ...

    def approval_request(self, plan_id: str, tickets: Sequence[ApprovalTicket]) -> None: ...

    def done(self, message_id: str, text: str, sources: Sequence[CitedSource]) -> None: ...


class NoChatEvents:
    def status(self, state: PetState) -> None:
        return None

    def chunk(self, message_id: str, text: str) -> None:
        return None

    def approval_request(self, plan_id: str, tickets: Sequence[ApprovalTicket]) -> None:
        return None

    def done(self, message_id: str, text: str, sources: Sequence[CitedSource]) -> None:
        return None


class EventApprovalGate:
    """把草案推给前端并阻塞等待裁决，超时按拒绝处理。"""

    def __init__(self, events: ChatEvents, registry: ApprovalRegistry) -> None:
        self._events = events
        self._registry = registry

    def request(self, plan_id: str, drafts: Sequence[ApprovalDraft]) -> dict[str, ApprovalOutcome]:
        tickets = self._registry.open(drafts)
        self._events.status("executing")
        self._events.approval_request(plan_id, tickets)
        return self._registry.wait(tickets)


@dataclass(frozen=True)
class ChatRequest:
    user_text: str | None = None
    fragments: Sequence[MemoryFragment] = ()
    trigger_reason: str | None = None
    allow_dispatch: bool = True
    events: ChatEvents | None = None


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


class ModelOutputError(Exception):
    pass


class DispatchRejectedError(ModelOutputError):
    pass


@dataclass(frozen=True)
class ChatOutput:
    kind: Literal["chat", "dispatch"]
    text: str = ""
    plan: tuple[PlanStep, ...] = ()


def parse_chat_output(content: str) -> ChatOutput:
    try:
        data = json.loads(content)
    except ValueError as exc:
        raise ModelOutputError("回复不是合法的 JSON") from exc
    if not isinstance(data, dict) or not data:
        raise ModelOutputError("回复必须是 JSON 对象")
    if next(iter(data)) != "type":
        raise ModelOutputError("type 必须是第一个字段")
    if data["type"] == "dispatch":
        if set(data) != {"type", "plan"}:
            raise DispatchRejectedError("dispatch 只能包含 type 与 plan 两个字段")
        try:
            plan = parse_plan_data(data["plan"])
        except PlanError as exc:
            raise DispatchRejectedError(f"派发计划不合法：{exc}") from exc
        return ChatOutput(kind="dispatch", plan=tuple(plan))
    if data["type"] != "chat":
        raise ModelOutputError(f"type 不合法：{data['type']}")
    if set(data) != {"type", "text"}:
        raise ModelOutputError("chat 只能包含 type 与 text 两个字段")
    text = data["text"]
    if not isinstance(text, str) or not text.strip():
        raise ModelOutputError("text 必须是非空字符串")
    if not split_citations(text.strip())[0]:
        raise ModelOutputError("text 为空，只有引用标记")
    return ChatOutput(kind="chat", text=text.strip())


def _attempted_capability(content: str) -> str:
    try:
        data = json.loads(content)
    except ValueError:
        return UNSPECIFIED_CAPABILITY
    plan = data.get("plan") if isinstance(data, dict) else None
    if isinstance(plan, list):
        for item in plan:
            capability = item.get("capability") if isinstance(item, dict) else None
            if isinstance(capability, str) and capability.strip():
                return capability.strip()
    return UNSPECIFIED_CAPABILITY


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
        structured: StructuredMemoryDoc | None = None,
        timezone: tzinfo | None = None,
        clock: Callable[[tzinfo], datetime] | None = None,
        capabilities: Sequence[CapabilityDescription] = (),
        orchestrator: Orchestrator | None = None,
        approvals: ApprovalRegistry | None = None,
        perception: PerceptionReader | None = None,
        activity: ChatActivity | None = None,
        interactions: InteractionReader | None = None,
    ) -> None:
        self._llm = llm
        self._daily_log = daily_log
        self._usage_sink = usage_sink
        self._events = events or NoChatEvents()
        self._personas = personas
        self._templates = templates
        self._structured = structured
        self._timezone = timezone or DEFAULT_TIMEZONE
        self._clock = clock
        self._capabilities = tuple(capabilities)
        self._orchestrator = orchestrator
        self._approvals = approvals
        self._perception = perception
        self._activity = activity
        self._interactions = interactions
        self._transcript = ChatTranscript(self._timezone, clock=self._clock)

    @property
    def approvals(self) -> ApprovalRegistry | None:
        return self._approvals

    def _gate(self, events: ChatEvents) -> ApprovalGate:
        if self._approvals is None:
            return AutoApprovalGate()
        return EventApprovalGate(events, self._approvals)

    def chat(self, request: ChatRequest) -> ChatReply:
        if self._activity is None:
            return self._run(request)
        with self._activity:
            return self._run(request)

    def _run(self, request: ChatRequest) -> ChatReply:
        user_text = (request.user_text or "").strip() or None
        trigger_reason = (request.trigger_reason or "").strip() or None
        if user_text is None and trigger_reason is None:
            raise ChatError("empty_input", "消息不能为空")
        events = request.events or self._events
        snapshot = self._current_persona()
        builder = ContextBuilder(
            snapshot.config,
            self._templates,
            self._timezone,
            structured=self._structured,
            clock=self._clock,
            capabilities=self._capabilities,
            perception=self._perception,
            transcript=self._transcript,
        )
        assembled = builder.build(
            user_text,
            request.fragments,
            trigger_reason=trigger_reason,
            interaction_lines=self._interaction_lines(user_text is not None),
        )
        messages = assembled.messages
        digest = _prompt_digest(assembled.template_digest, snapshot.id, snapshot.version)
        try:
            client = self._llm.client_for("chat")
        except LlmUnavailableError as exc:
            self._record_unavailable(digest)
            raise ChatError("model_unavailable", str(exc)) from exc

        events.status("thinking")
        try:
            output = self._generate(client, messages, digest, allow_dispatch=request.allow_dispatch)
            if output.kind == "dispatch":
                return self._reply_from_dispatch(
                    client, messages, digest, output, user_text, assembled, events
                )
            return self._finish_reply(user_text, output.text, assembled, events)
        finally:
            events.status("idle")

    def _finish_reply(
        self,
        user_text: str | None,
        raw: str,
        assembled: AssembledContext,
        events: ChatEvents,
    ) -> ChatReply:
        reply, cited = split_citations(raw)
        recorded = self._record(user_text, reply)
        if recorded:
            self._remember(user_text, reply, assembled.notes)
        message_id = uuid4().hex
        sources = tuple(resolve_sources(cited, assembled.injected))
        events.status("speaking")
        for segment in split_segments(reply):
            events.chunk(message_id, segment)
        events.done(message_id, reply, sources)
        return ChatReply(message_id=message_id, text=reply, recorded=recorded, sources=sources)

    def _reply_from_dispatch(
        self,
        client: TrackedChatModel,
        messages: list[BaseMessage],
        digest: str,
        output: ChatOutput,
        user_text: str | None,
        assembled: AssembledContext,
        events: ChatEvents,
    ) -> ChatReply:
        if self._orchestrator is None:
            raise ChatError("invalid_output", "派发执行器未配置，只能输出 chat")
        events.status("executing")
        plan_id = uuid4().hex
        checkpoints = self._orchestrator.run_plan(
            output.plan, gate=self._gate(events), plan_id=plan_id
        )
        self._record_rejections(checkpoints)
        blocks = "\n".join(checkpoint_block(plan_id, item) for item in checkpoints)
        follow_up = [
            *messages,
            AIMessage(
                content=json.dumps(
                    {
                        "type": "dispatch",
                        "plan": [
                            {"capability": step.capability, "task": step.task}
                            for step in output.plan
                        ],
                    },
                    ensure_ascii=False,
                )
            ),
            HumanMessage(content=CHECKPOINT_INTRO.format(blocks=blocks)),
        ]
        events.status("thinking")
        generated = self._generate(client, follow_up, digest, allow_dispatch=False)
        return self._finish_reply(user_text, generated.text, assembled, events)

    def _record(self, user_text: str | None, reply_text: str) -> bool:
        if user_text is None:
            return self._daily_log.record_reply(reply_text)
        return self._daily_log.record_turn(user_text, reply_text)

    def _remember(self, user_text: str | None, reply_text: str, notes: Sequence[ChatLine]) -> None:
        for note in notes:
            self._transcript.add(note)
        if user_text is None:
            self._transcript.append_reply(reply_text)
        else:
            self._transcript.append_turn(user_text, reply_text)

    def _current_persona(self) -> PersonaSnapshot:
        try:
            return self._personas.current()
        except PersonaUnavailableError as exc:
            raise ChatError(PersonaUnavailableError.code, str(exc)) from exc

    def _interaction_lines(self, user_turn: bool) -> tuple[str, ...]:
        if not user_turn or self._interactions is None:
            return ()
        return tuple(f"[master] {item.label}" for item in self._interactions.drain())

    def history(
        self,
        *,
        keyword: str | None,
        date_from: str | None,
        date_to: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DailyLogView], bool]:
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
        *,
        allow_dispatch: bool = True,
    ) -> ChatOutput:
        failure: ChatError | None = None
        for _ in range(MAX_ATTEMPTS):
            content = self._invoke(client, messages, digest)
            try:
                output = parse_chat_output(content)
            except DispatchRejectedError as exc:
                self._record_dispatch_failed(content, str(exc))
                failure = ChatError("invalid_output", f"模型输出不符合规定：{exc}")
                continue
            except ModelOutputError as exc:
                failure = ChatError("invalid_output", f"模型输出不符合规定：{exc}")
                continue
            if output.kind == "dispatch":
                if not allow_dispatch:
                    failure = ChatError(
                        "invalid_output", "模型输出不符合规定：派发之后只能输出 chat，不能再次派发"
                    )
                    continue
                return output
            body, _ = split_citations(output.text)
            if overlong_sentences(body):
                failure = ChatError(
                    "sentence_too_long",
                    f"回复中有单句超过 {SENTENCE_MAX_CHARS} 字符，重新生成后仍超限",
                )
                continue
            return output
        assert failure is not None
        raise failure

    def _record_rejections(self, checkpoints: Sequence[Checkpoint]) -> None:
        for checkpoint in checkpoints:
            if checkpoint.status != STATUS_REJECTED:
                continue
            try:
                self._remember_event(
                    "approval_rejected",
                    capability=checkpoint.capability,
                    reason=checkpoint.error or "",
                )
            except Exception:
                logger.exception("审批拒绝事件写入失败")

    def _record_dispatch_failed(self, content: str, reason: str) -> None:
        try:
            self._remember_event(
                "dispatch_failed",
                capability=_attempted_capability(content),
                reason=reason,
            )
        except Exception:
            logger.exception("派发失败事件写入失败")

    def _remember_event(self, event_type: str, **fields: str) -> None:
        content = encode_event(event_type, **fields)
        if not self._daily_log.record_event(event_type, **fields):
            return
        self._transcript.add(
            ChatLine(
                at=self._now(),
                message=AIMessage(content=f"[事件] {event_text(content)}"),
            )
        )

    def _now(self) -> datetime:
        clock = self._clock or (lambda zone: datetime.now(zone))
        return clock(self._timezone)

    def _invoke(self, client: TrackedChatModel, messages: list[BaseMessage], digest: str) -> str:
        with usage_scope("chat", prompt_hash=digest):
            try:
                response = client.invoke(messages)
            except Exception as exc:
                raise ChatError("model_failed", f"模型调用失败：{exc}") from exc
        return _text_of(response.content).strip()

    def _record_unavailable(self, digest: str) -> None:
        active = self._llm.active_model("chat")
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        record = UsageSample(
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
