from collections.abc import Sequence
from datetime import datetime

from api.schemas.approval import ApprovalItem, FilePreview, PreviewHunk
from api.schemas.chat import ReplyMessageOut, SourceOut
from api.schemas.events import (
    ApprovalRequestEvent,
    ChatChunkEvent,
    ChatDoneEvent,
    ProactiveMessageEvent,
    StatusEvent,
    UsageCallFinishedEvent,
    UsageCallOut,
    UsageCallStartedEvent,
    UsagePanelSnapshotEvent,
    UsagePanelTurnOut,
    UsageTurnFinishedEvent,
    UsageTurnStartedEvent,
)
from infra.events.bus import EventBus
from infra.llm.usage import CallStart, UsageSample
from module.agent.approval import ApprovalTicket
from module.chat.chat_service import PetState, ReplyMessage
from module.chat.citations import CitedSource
from module.llm.usage_panel import PanelCall, PanelTurn, UsagePanel, record_to_call
from module.llm.usage_service import TurnUsage


def source_out(source: CitedSource) -> SourceOut:
    return SourceOut(
        id=source.id,
        kind=source.kind,
        date=source.date,
        text=source.text,
    )


def reply_message_out(message: ReplyMessage) -> ReplyMessageOut:
    return ReplyMessageOut(
        message_id=message.message_id,
        text=message.text,
        at=message.at,
        sources=[source_out(source) for source in message.sources],
    )


def joined_text(messages: Sequence[ReplyMessage]) -> str:
    """旧字段 text 的取值：多条发言用换行拼接，保持与后端 reply.text 一致。"""
    return "\n".join(message.text for message in messages)


def approval_item(ticket: ApprovalTicket) -> ApprovalItem:
    """把一张票据投影成前端卡片；读取票据额外带动作、目标与参数摘要。"""
    draft = ticket.draft
    return ApprovalItem(
        approval_id=ticket.approval_id,
        run_id=draft.run_id,
        topic=draft.topic,
        task=draft.task,
        content=draft.content,
        tools=list(draft.tools),
        persona_id=draft.persona_id,
        persona_version=draft.persona_version,
        main_task_id=draft.main_task_id,
        subtask_id=draft.subtask_id,
        source=draft.source,
        previews=[
            FilePreview(
                resource=preview.resource,
                path=preview.path,
                op=preview.op,
                created=preview.created,
                deleted=preview.deleted,
                authors=list(preview.authors),
                hunk_count=preview.hunk_count,
                folded=preview.folded,
                hunks=[
                    PreviewHunk(
                        header=hunk.header,
                        removed=list(hunk.removed),
                        added=list(hunk.added),
                    )
                    for hunk in preview.hunks
                ],
            )
            for preview in draft.previews
        ],
        model=draft.model,
        action=draft.action,
        resource=draft.resource,
        path=draft.path,
        parameters=draft.parameters,
        plan_id=draft.main_task_id,
    )


def approval_event(plan_id: str, tickets: Sequence[ApprovalTicket]) -> ApprovalRequestEvent:
    return ApprovalRequestEvent(
        plan_id=plan_id, items=[approval_item(ticket) for ticket in tickets]
    )


class BusEventPublisher:
    def __init__(
        self,
        bus: EventBus,
        panel: UsagePanel | None = None,
        dev_mode: bool = False,
    ) -> None:
        self._bus = bus
        self._panel = panel or UsagePanel()
        self._dev_mode = dev_mode
        self._last_status = StatusEvent(state="idle")

    @property
    def last_status(self) -> StatusEvent:
        """最近一次发布的状态，供新连接回放初始快照。"""
        return self._last_status

    @property
    def panel_snapshot(self) -> UsagePanelSnapshotEvent:
        """当前轮的完整快照；重连时先发它，前端据此丢弃旧轮。"""
        turn = self._panel.snapshot()
        return UsagePanelSnapshotEvent(
            mode="dev" if self._dev_mode else "prod",
            turn=self._panel_turn_out(turn) if turn is not None else None,
        )

    def status(self, state: PetState) -> None:
        event = StatusEvent(state=state)
        self._last_status = event
        self._bus.publish(event)

    def chunk(self, message_id: str, text: str) -> None:
        self._bus.publish(ChatChunkEvent(message_id=message_id, text=text))

    def approval_request(self, plan_id: str, tickets: Sequence[ApprovalTicket]) -> None:
        self._bus.publish(approval_event(plan_id, tickets))

    def done(
        self, message_id: str, messages: Sequence[ReplyMessage], notices: Sequence[str] = ()
    ) -> None:
        self._bus.publish(
            ChatDoneEvent(
                message_id=message_id,
                text=joined_text(messages),
                sources=[source_out(source) for message in messages for source in message.sources],
                messages=[reply_message_out(message) for message in messages],
                notices=list(notices),
            )
        )

    def proactive_message(
        self,
        message_id: str,
        messages: Sequence[ReplyMessage],
        topic: str,
        at: datetime,
        notices: Sequence[str] = (),
    ) -> None:
        self._bus.publish(
            ProactiveMessageEvent(
                id=message_id,
                text=joined_text(messages),
                topic=topic,
                at=at,
                messages=[reply_message_out(message) for message in messages],
                notices=list(notices),
            )
        )

    def call_started(self, event: CallStart) -> None:
        self._panel.call_started(event)
        self._bus.publish(
            UsageCallStartedEvent(
                call=self._call_out(
                    PanelCall(
                        provider=event.provider,
                        model=event.model,
                        purpose=event.purpose,
                        turn_id=event.turn_id,
                        run_id=event.run_id,
                        prompt_hash=event.prompt_hash,
                        started_at=event.started_at,
                        prompt_text=event.prompt_text,
                    )
                )
            )
        )

    def call_finished(self, sample: UsageSample) -> None:
        self._panel.call_finished(sample)
        self._bus.publish(
            UsageCallFinishedEvent(
                call=self._call_out(
                    PanelCall(
                        provider=sample.provider,
                        model=sample.model,
                        purpose=sample.purpose,
                        turn_id=sample.turn_id,
                        run_id=sample.run_id,
                        prompt_hash=sample.prompt_hash,
                        started_at=sample.started_at,
                        finished_at=sample.finished_at,
                        tokens_in=sample.tokens_in,
                        tokens_out=sample.tokens_out,
                        usage_raw=sample.usage_raw,
                        latency_ms=sample.latency_ms,
                        success=sample.success,
                        prompt_text=sample.prompt_text,
                        reply_text=sample.reply_text,
                        reply_raw=sample.reply_raw,
                    )
                )
            )
        )

    def turn_started(self, turn_id: str, at: str) -> None:
        self._panel.start_turn(turn_id, at)
        self._bus.publish(UsageTurnStartedEvent(turn_id=turn_id, at=at))

    def turn_finished(self, usage: TurnUsage) -> None:
        self._panel.finish_turn(usage)
        self._bus.publish(
            UsageTurnFinishedEvent(
                turn_id=usage.turn_id,
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                tokens_complete=usage.tokens_complete,
                model_time_ms=usage.model_time_ms,
                started_at=usage.started_at,
                finished_at=usage.finished_at,
                calls=[self._call_out(record_to_call(record)) for record in usage.calls],
            )
        )

    def _panel_turn_out(self, turn: PanelTurn) -> UsagePanelTurnOut:
        return UsagePanelTurnOut(
            turn_id=turn.turn_id,
            started_at=turn.started_at,
            finished_at=turn.finished_at,
            finished=turn.finished,
            tokens_in=turn.tokens_in,
            tokens_out=turn.tokens_out,
            tokens_complete=turn.tokens_complete,
            model_time_ms=turn.model_time_ms,
            calls=[self._call_out(call) for call in turn.calls],
        )

    def _call_out(self, call: PanelCall) -> UsageCallOut:
        # 展示边界必须在后端：正式模式即使前端被绕过，也拿不到正文与调用方信息。
        visible = self._dev_mode
        return UsageCallOut(
            provider=call.provider if visible else None,
            model=call.model if visible else None,
            purpose=call.purpose,
            turn_id=call.turn_id,
            run_id=call.run_id if visible else None,
            prompt_hash=call.prompt_hash if visible else None,
            started_at=call.started_at,
            finished_at=call.finished_at,
            tokens_in=call.tokens_in,
            tokens_out=call.tokens_out,
            usage_raw=call.usage_raw if visible else None,
            latency_ms=call.latency_ms,
            success=call.success,
            prompt_text=call.prompt_text if visible else None,
            reply_text=call.reply_text if visible else None,
            reply_raw=call.reply_raw if visible else None,
        )
