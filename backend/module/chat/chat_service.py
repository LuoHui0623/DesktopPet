import json
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from hashlib import sha256
from typing import Any, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from constants.speech import SENTENCE_MAX_WIDTH, visible_width
from infra.llm.tokens import TokenCountError
from infra.llm.usage import TrackedChatModel, UsageSample, UsageSink, usage_scope
from module.agent.approval import (
    ApprovalDraft,
    ApprovalGate,
    ApprovalOutcome,
    ApprovalRegistry,
    ApprovalTicket,
    AutoApprovalGate,
)
from module.agent.chat_output import (
    ChatOutput,
    DispatchRejectedError,
    ModelOutputError,
    _extract_json,
    parse_chat_output,
)
from module.agent.context import SOURCE_SYSTEM_AUTO, SOURCE_USER
from module.agent.orchestrator import (
    ERROR_REJECTED,
    STATUS_REJECTED,
    Checkpoint,
    Orchestrator,
)
from module.agent.prompt import (
    CapabilityDescription,
    PromptTemplates,
    ResourceItem,
    build_system_prompt,
)
from module.chat.activity import ChatActivity
from module.chat.citations import CitedSource, resolve_sources, split_citations
from module.chat.context import (
    AssembledContext,
    ContextBuilder,
    MemoryFragment,
    PerceptionReader,
    visible_notices,
)
from module.chat.transcript import ChatLine, ChatTranscript
from module.interaction.contracts import InteractionReader
from module.llm import LlmService, LlmUnavailableError, UsageService
from module.memory.contracts import DailyLogView
from module.memory.daily_log_service import DailyLogService
from module.memory.memory_service import StructuredMemoryDoc
from module.memory.recollection_service import RecollectionService
from module.persona.persona_service import PersonaService, PersonaSnapshot, PersonaUnavailableError

logger = logging.getLogger("pet")

SENTENCE_ENDS = frozenset("。！？!?…")
NEWLINE = "\n"
# 英文缩写后的点不收句，其余英文句号按句末处理。
ABBREVIATIONS = frozenset(
    {"mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "etc", "e.g", "i.e"}
)
MAX_ATTEMPTS = 2
DEFAULT_TIMEZONE = ZoneInfo("Asia/Shanghai")
UNSPECIFIED_TOPIC = "（未指定）"
CONTEXT_OVERFLOW_MARKERS = (
    "maximum context length",
    "context length exceeded",
    "context window",
    "too many tokens",
)
FOLLOW_UP_PROMPT = "本次派发已执行完毕。现在只用一条 chat 回复主人，把结果告诉主人；不要再派发。"

PetState = Literal["idle", "thinking", "speaking", "executing", "sleeping"]


class ChatError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ChatEvents(Protocol):
    def status(self, state: PetState) -> None: ...

    def chunk(self, message_id: str, text: str) -> None: ...

    def approval_request(self, plan_id: str, tickets: Sequence[ApprovalTicket]) -> None: ...

    def done(
        self, message_id: str, messages: Sequence["ReplyMessage"], notices: Sequence[str] = ()
    ) -> None: ...


class NoChatEvents:
    def status(self, state: PetState) -> None:
        return None

    def chunk(self, message_id: str, text: str) -> None:
        return None

    def approval_request(self, plan_id: str, tickets: Sequence[ApprovalTicket]) -> None:
        return None

    def done(
        self, message_id: str, messages: Sequence["ReplyMessage"], notices: Sequence[str] = ()
    ) -> None:
        return None


class EventApprovalGate:
    """把草案推给前端并阻塞等待裁决；不设超时，任务挂起直到决出或取消。"""

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
    read_only_dispatch: bool = False
    events: ChatEvents | None = None


@dataclass(frozen=True)
class ReplyMessage:
    """一次发言：一轮回复里可以有多条，各自带自己的 id、时间与引用来源。"""

    message_id: str
    text: str
    at: datetime
    sources: tuple[CitedSource, ...] = ()


@dataclass(frozen=True)
class ChatReply:
    message_id: str
    messages: tuple[ReplyMessage, ...] = ()
    recorded: bool = True
    # 用户可见的系统提示 code；只有 memory_unloaded 会经此通道出现（D-30）。
    notices: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(message.text for message in self.messages)

    @property
    def sources(self) -> tuple[CitedSource, ...]:
        seen: dict[str, CitedSource] = {}
        for message in self.messages:
            for source in message.sources:
                seen.setdefault(source.id, source)
        return tuple(seen.values())


def _is_sentence_period(text: str, index: int) -> bool:
    """判断 text[index] 处的英文句号是否收句。"""
    before = text[index - 1] if index else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    if before.isdigit() and after.isdigit():
        return False
    if after == ".":
        return False
    if before.isalpha() and after.isalpha():
        return False
    head = text[:index].rstrip()
    word = head.rsplit(None, 1)[-1] if head else ""
    if len(word) <= 1 and word.isalpha():
        return False
    return word.rstrip(".").lower() not in ABBREVIATIONS


def _ends_sentence(text: str, index: int) -> bool:
    char = text[index]
    if char in SENTENCE_ENDS or char == NEWLINE:
        return True
    return char == "." and _is_sentence_period(text, index)


def _split_ranges(text: str) -> list[tuple[int, int]]:
    """把文本切成连续覆盖全文的句段区间，拼回去与原文完全一致。"""
    ranges: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(text):
        if not _ends_sentence(text, index):
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end] in SENTENCE_ENDS:
            end += 1
        ranges.append((start, end))
        start = end
        index = end
    if start < len(text):
        ranges.append((start, len(text)))
    return ranges


def split_sentences(text: str) -> list[str]:
    return [
        text[start:end].strip() for start, end in _split_ranges(text) if text[start:end].strip()
    ]


def split_segments(text: str) -> list[str]:
    return [text[start:end] for start, end in _split_ranges(text) if text[start:end]]


def overlong_sentences(text: str) -> list[str]:
    return [
        sentence
        for sentence in split_sentences(text)
        if visible_width(sentence) > SENTENCE_MAX_WIDTH
    ]


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


def _attempted_topic(content: str) -> str:
    try:
        data = json.loads(_extract_json(content))
    except ValueError:
        return UNSPECIFIED_TOPIC
    plan = data.get("plan") if isinstance(data, dict) else None
    if isinstance(plan, list):
        for item in plan:
            topic = item.get("topic") if isinstance(item, dict) else None
            if isinstance(topic, str) and topic.strip():
                return topic.strip()
    return UNSPECIFIED_TOPIC


def _prompt_digest(template_digest: str, persona_id: str, persona_version: int) -> str:
    return sha256(f"{template_digest}|{persona_id}|{persona_version}".encode()).hexdigest()


def _is_context_overflow(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in CONTEXT_OVERFLOW_MARKERS)


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
        resources: Sequence[ResourceItem] | None = None,
        orchestrator: Orchestrator | None = None,
        approvals: ApprovalRegistry | None = None,
        perception: PerceptionReader | None = None,
        activity: ChatActivity | None = None,
        interactions: InteractionReader | None = None,
        usage: UsageService | None = None,
        recollection: RecollectionService | None = None,
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
        self._resources = None if resources is None else tuple(resources)
        self._orchestrator = orchestrator
        self._approvals = approvals
        self._perception = perception
        self._activity = activity
        self._interactions = interactions
        self._usage = usage
        self._recollection = recollection
        # 被动 query 的 session 级占位：同一时刻只处理一个用户回合（D-22）。
        self._turn_slot = threading.Lock()
        self._transcript = ChatTranscript(
            self._timezone, clock=self._clock, loader=self._restore_lines
        )

    @property
    def approvals(self) -> ApprovalRegistry | None:
        return self._approvals

    def _gate(self, events: ChatEvents) -> ApprovalGate:
        if self._approvals is None:
            return AutoApprovalGate()
        return EventApprovalGate(events, self._approvals)

    def chat(self, request: ChatRequest) -> ChatReply:
        # 主动轮不绑定用户输入，不占位、不受串行约束（Q-16）。
        if request.user_text is not None:
            if not self._turn_slot.acquire(blocking=False):
                raise ChatError("session_busy", "上一轮对话还在处理中")
            try:
                return self._with_activity(request)
            finally:
                self._turn_slot.release()
        return self._with_activity(request)

    def _with_activity(self, request: ChatRequest) -> ChatReply:
        if self._activity is None:
            return self._run(request)
        with self._activity:
            return self._run(request)

    def _run(self, request: ChatRequest) -> ChatReply:
        """一轮对话从开始到收尾共用一个 turn_id，模型失败也要汇总出结果。"""
        user_text = (request.user_text or "").strip() or None
        trigger_reason = (request.trigger_reason or "").strip() or None
        if user_text is None and trigger_reason is None:
            raise ChatError("empty_input", "消息不能为空")
        received_at = self._now() if user_text is not None else None
        turn_id = self._begin_turn(received_at)
        try:
            return self._run_turn(request, user_text, trigger_reason, received_at, turn_id)
        finally:
            self._end_turn(turn_id)

    def _run_turn(
        self,
        request: ChatRequest,
        user_text: str | None,
        trigger_reason: str | None,
        received_at: datetime | None,
        turn_id: str | None,
    ) -> ChatReply:
        events = request.events or self._events
        snapshot = self._current_persona()
        self._transcript.set_persona(snapshot.id)
        # 先把主人这句落库：回忆要读当天聊天记录，模型不可用时这句也不能丢。
        current_record = None
        if user_text is not None:
            try:
                current_record = self._daily_log.record_user(
                    user_text, at=received_at, persona_id=snapshot.id
                )
            except Exception as exc:
                # 落库是回复的必要条件：失败即终止本轮，不调模型、不回复（D-21）。
                logger.exception("本轮输入落库失败")
                raise ChatError("record_failed", "本轮输入没能存下来") from exc
        persisted = user_text is None or current_record is not None
        # 回忆可能等待较久，落库之后、拼上下文之前等，跨零点则给新的一天补一次。
        recollection = self._await_recollection(snapshot, turn_id)
        # 先构建 chat 系统提示词，再交给上下文构建器拼装历史与记忆（D2、Q4）。
        prompt = build_system_prompt(
            "chat",
            snapshot.config,
            self._templates,
            capabilities=self._capabilities,
            resources=self._resources,
        )
        builder = ContextBuilder(
            snapshot.config,
            self._templates,
            self._timezone,
            structured=self._structured,
            clock=self._clock,
            capabilities=self._capabilities,
            resources=self._resources,
            perception=self._perception,
            transcript=self._transcript,
            base_prompt=prompt,
            recollection=recollection,
        )
        assembled = builder.build(
            user_text,
            request.fragments,
            trigger_reason=trigger_reason,
            interaction_lines=self._interaction_lines(user_text is not None),
            received_at=received_at,
            current_record=current_record,
        )
        messages = assembled.messages
        digest = _prompt_digest(assembled.template_digest, snapshot.id, snapshot.version)
        try:
            client = self._llm.client_for("chat")
        except LlmUnavailableError as exc:
            self._record_unavailable(digest, turn_id)
            raise ChatError("model_unavailable", str(exc)) from exc

        events.status("thinking")
        try:
            output = self._generate(
                client,
                messages,
                digest,
                allow_dispatch=request.allow_dispatch,
                turn_id=turn_id,
            )
            if output.kind == "dispatch":
                return self._reply_from_dispatch(
                    client,
                    messages,
                    digest,
                    output,
                    assembled,
                    events,
                    persona=snapshot,
                    persisted=persisted,
                    read_only=request.read_only_dispatch,
                    source=SOURCE_USER if user_text is not None else SOURCE_SYSTEM_AUTO,
                    turn_id=turn_id,
                )
            return self._finish_reply(
                output.texts,
                assembled,
                events,
                persona_id=snapshot.id,
                persisted=persisted,
            )
        finally:
            events.status("idle")

    def _await_recollection(self, snapshot: PersonaSnapshot, turn_id: str | None) -> str:
        """等回忆就绪并返回可注入上下文的正文；跨零点则给新的一天补一次。"""
        if self._recollection is None:
            return ""
        today = self._now().date()
        recall = self._recollection.ensure(today, persona=snapshot, turn_id=turn_id)
        # 等待生成期间可能已经跨日：新的一天要再补一次，再按最新结果发言。
        after = self._now().date()
        if after != today:
            recall = self._recollection.ensure(after, persona=snapshot, turn_id=turn_id)
        if not recall.ok:
            return ""
        return self._recollection_line(recall.day, snapshot.id)

    def _recollection_line(self, day: str, persona_id: str) -> str:
        entry = self._daily_log.read_recollection(day, persona_id=persona_id)
        if entry is None:
            return ""
        clock = f"{entry.created_at.astimezone(self._timezone):%H:%M:%S}"
        return f"[assistant][{clock}]{entry.content}"

    def _finish_reply(
        self,
        texts: Sequence[str],
        assembled: AssembledContext,
        events: ChatEvents,
        *,
        persona_id: str,
        persisted: bool,
    ) -> ChatReply:
        replied_at = self._now()
        replies: list[str] = []
        citations: list[list[str]] = []
        for text in texts:
            reply, cited = split_citations(text)
            replies.append(reply)
            citations.append(cited)
        recorded = persisted and self._daily_log.record_reply(
            replies, at=replied_at, persona_id=persona_id
        )
        message_id = uuid4().hex
        messages = tuple(
            ReplyMessage(
                message_id=uuid4().hex,
                text=reply,
                at=replied_at,
                sources=tuple(resolve_sources(cited, assembled.injected)),
            )
            for reply, cited in zip(replies, citations, strict=True)
        )
        # 记忆降级不占用回复正文，经系统状态通道告诉用户（D-30）。
        notices = visible_notices(assembled.warnings)
        events.status("speaking")
        for segment in split_segments("\n".join(replies)):
            events.chunk(message_id, segment)
        events.done(message_id, messages, notices)
        return ChatReply(
            message_id=message_id, messages=messages, recorded=recorded, notices=notices
        )

    def _reply_from_dispatch(
        self,
        client: TrackedChatModel,
        messages: list[BaseMessage],
        digest: str,
        output: ChatOutput,
        assembled: AssembledContext,
        events: ChatEvents,
        *,
        persona: PersonaSnapshot,
        persisted: bool,
        read_only: bool = False,
        source: str = SOURCE_USER,
        turn_id: str | None = None,
    ) -> ChatReply:
        if self._orchestrator is None:
            raise ChatError("invalid_output", "派发执行器未配置，只能输出 chat")
        events.status("executing")
        plan_id = uuid4().hex
        gate = AutoApprovalGate() if read_only else self._gate(events)
        checkpoints = self._orchestrator.run_plan(
            output.plan,
            gate=gate,
            persona=persona,
            source=source,
            plan_id=plan_id,
            read_only=read_only,
            turn_id=turn_id,
        )
        self._record_rejections(checkpoints)
        merged = self._record_assistant_backfill(checkpoints, persona_id=persona.id)
        follow_up = [*messages]
        if merged:
            follow_up.append(AIMessage(content=merged))
        follow_up.append(HumanMessage(content=FOLLOW_UP_PROMPT))
        events.status("thinking")
        generated = self._generate(client, follow_up, digest, allow_dispatch=False, turn_id=turn_id)
        return self._finish_reply(
            generated.texts, assembled, events, persona_id=persona.id, persisted=persisted
        )

    def _restore_lines(self, day: date, persona_id: str) -> list[ChatLine]:
        """把当天已落库的聊天行还原成带角色的时间线，事件与系统消息不进入上下文。"""
        lines: list[ChatLine] = []
        for entry in self._daily_log.restore_day(day.isoformat(), persona_id=persona_id):
            if entry.role == "master":
                lines.append(
                    ChatLine(
                        at=entry.created_at,
                        message=HumanMessage(content=entry.content),
                        role="master",
                        record_id=entry.id,
                        record_day=entry.event_time.date(),
                    )
                )
            else:
                lines.append(
                    ChatLine(
                        at=entry.created_at,
                        message=AIMessage(content=entry.content),
                        is_reply=True,
                        role=entry.role,
                        record_id=entry.id,
                        record_day=entry.event_time.date(),
                    )
                )
        return lines

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
        turn_id: str | None = None,
    ) -> ChatOutput:
        self._check_budget(client, messages)
        failure: ChatError | None = None
        for _ in range(MAX_ATTEMPTS):
            content = self._invoke(client, messages, digest, turn_id)
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
            overlong = [
                sentence
                for text in output.texts
                for sentence in overlong_sentences(split_citations(text)[0])
            ]
            if overlong:
                failure = ChatError(
                    "sentence_too_long",
                    f"回复中有单句超过 {SENTENCE_MAX_WIDTH} 显示宽度的上限，重新生成后仍超限",
                )
                continue
            return output
        assert failure is not None
        raise failure

    def _check_budget(self, client: TrackedChatModel, messages: list[BaseMessage]) -> None:
        window = client.context_window
        max_tokens = client.max_tokens
        if not window or max_tokens is None:
            return
        try:
            counted = client.count_tokens(messages)
        except TokenCountError as exc:
            raise ChatError("token_count_failed", f"无法统计上下文 tokens：{exc}") from exc
        if counted.tokens + max_tokens <= window:
            return
        label = "估算 " if counted.estimated else ""
        raise ChatError(
            "context_overflow",
            f"上下文超出窗口：输入 {label}{counted.tokens} tokens + "
            f"回复上限 {max_tokens} tokens，超过模型窗口 {window}",
        )

    def _record_assistant_backfill(
        self, checkpoints: Sequence[Checkpoint], *, persona_id: str
    ) -> str:
        """本轮所有子代理结果（含失败/拒绝原因）合并成一条 assistant 行并回传正文。

        回注与落库共用这一份正文：后续回合读到的记录就是二次生成收到的内容（D-24/D-27）。
        存结果正文，不存检查点包装、plan_id 或通用提示。
        """
        merged = "\n".join(
            body
            for body in (
                ((item.output if item.ok else item.error) or "").strip() for item in checkpoints
            )
            if body
        )
        if merged:
            self._daily_log.record_assistant(merged, at=self._now(), persona_id=persona_id)
        return merged

    def _record_rejections(self, checkpoints: Sequence[Checkpoint]) -> None:
        for checkpoint in checkpoints:
            if checkpoint.status != STATUS_REJECTED:
                continue
            if checkpoint.error_code != ERROR_REJECTED:
                continue
            try:
                self._remember_event(
                    "approval_rejected",
                    topic=checkpoint.topic,
                    reason=checkpoint.error or "",
                )
            except Exception:
                logger.exception("审批拒绝事件写入失败")

    def _record_dispatch_failed(self, content: str, reason: str) -> None:
        try:
            self._remember_event(
                "dispatch_failed",
                topic=_attempted_topic(content),
                reason=reason,
            )
        except Exception:
            logger.exception("派发失败事件写入失败")

    def _remember_event(self, event_type: str, **fields: str) -> None:
        """诊断事件只写审计行，永不进入模型上下文（D-24/T-06）。"""
        self._daily_log.record_event(event_type, **fields)

    def _now(self) -> datetime:
        clock = self._clock or (lambda zone: datetime.now(zone))
        return clock(self._timezone)

    def _invoke(
        self,
        client: TrackedChatModel,
        messages: list[BaseMessage],
        digest: str,
        turn_id: str | None,
    ) -> str:
        with usage_scope("chat", prompt_hash=digest, turn_id=turn_id):
            try:
                response = client.invoke(messages)
            except Exception as exc:
                if _is_context_overflow(exc):
                    raise ChatError("context_overflow", f"模型报告上下文超限：{exc}") from exc
                raise ChatError("model_failed", f"模型调用失败：{exc}") from exc
        return _text_of(response.content).strip()

    def _record_unavailable(self, digest: str, turn_id: str | None) -> None:
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
            turn_id=turn_id,
        )
        try:
            self._usage_sink(record)
        except Exception:
            logger.exception("LLM 用量记录写入失败")

    def _begin_turn(self, received_at: datetime | None = None) -> str | None:
        if self._usage is None:
            return None
        at = received_at or self._now()
        return self._usage.start_turn(at.isoformat(timespec="milliseconds"))

    def _end_turn(self, turn_id: str | None) -> None:
        if turn_id is None or self._usage is None:
            return
        try:
            self._usage.finish_turn(turn_id)
        except Exception:
            logger.exception("轮次用量汇总失败：%s", turn_id)
