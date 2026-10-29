from datetime import datetime
from zoneinfo import ZoneInfo

from module.chat.chat_service import ChatError, ChatReply, ChatRequest
from module.proactive import ProactivePipeline, ProactiveResult
from service.perception.events import Trigger

TZ = ZoneInfo("Asia/Shanghai")
AT = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)
TRIGGER = Trigger("app_in_use.code", "使用 code 已 3 分钟")


class FakeChat:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatReply:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ChatReply(message_id=f"m{len(self.requests)}", text=outcome, recorded=True)


class FakeLedger:
    def __init__(self, *, fail: bool = False) -> None:
        self.topics: list[tuple[str, datetime | None]] = []
        self._fail = fail

    def record_sent(self, topic: str, at: datetime | None = None) -> object:
        if self._fail:
            raise OSError("db locked")
        self.topics.append((topic, at))
        return None


class FakePublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.messages: list[tuple[str, str, str, datetime]] = []
        self._fail = fail

    def proactive_message(self, message_id: str, text: str, topic: str, at: datetime) -> None:
        if self._fail:
            raise RuntimeError("socket closed")
        self.messages.append((message_id, text, topic, at))


def make_pipeline(
    chat: FakeChat,
    *,
    ledger: FakeLedger | None = None,
    publisher: FakePublisher | None = None,
    chat_active=None,
) -> tuple[ProactivePipeline, FakeLedger, FakePublisher]:
    ledger = ledger or FakeLedger()
    publisher = publisher or FakePublisher()
    pipeline = ProactivePipeline(chat, ledger, publisher, chat_active=chat_active, clock=lambda: AT)
    return pipeline, ledger, publisher


def test_success_generates_records_and_publishes_once():
    chat = FakeChat(["今天也辛苦啦。"])
    pipeline, ledger, publisher = make_pipeline(chat)

    result = pipeline.handle(TRIGGER)

    assert result == ProactiveResult(True, "app_in_use.code", "m1")
    [request] = chat.requests
    assert request.user_text is None
    assert request.trigger_reason == "使用 code 已 3 分钟"
    assert request.allow_dispatch is False
    assert request.events is not None
    assert ledger.topics == [("app_in_use.code", AT)]
    assert publisher.messages == [("m1", "今天也辛苦啦。", "app_in_use.code", AT)]


def test_active_chat_drops_the_trigger_without_calling_the_model():
    chat = FakeChat([])
    pipeline, ledger, publisher = make_pipeline(chat, chat_active=lambda: True)

    result = pipeline.handle(TRIGGER)

    assert result.reason == "chat_active"
    assert result.sent is False
    assert chat.requests == []
    assert ledger.topics == []
    assert publisher.messages == []


def test_model_unavailable_is_not_retried():
    chat = FakeChat([ChatError("model_unavailable", "未配置模型")])
    pipeline, ledger, publisher = make_pipeline(chat)

    result = pipeline.handle(TRIGGER)

    assert result.reason == "model_unavailable"
    assert len(chat.requests) == 1
    assert ledger.topics == []
    assert publisher.messages == []


def test_model_failure_retries_the_whole_round_once():
    chat = FakeChat([ChatError("model_failed", "超时"), "缓过来了。"])
    pipeline, ledger, publisher = make_pipeline(chat)

    result = pipeline.handle(TRIGGER)

    assert result.sent is True
    assert len(chat.requests) == 2
    assert ledger.topics == [("app_in_use.code", AT)]
    assert len(publisher.messages) == 1


def test_retry_stops_after_one_extra_round():
    chat = FakeChat(
        [ChatError("invalid_output", "不是 JSON"), ChatError("invalid_output", "不是 JSON")]
    )
    pipeline, ledger, publisher = make_pipeline(chat)

    result = pipeline.handle(TRIGGER)

    assert result.reason == "invalid_output"
    assert result.sent is False
    assert len(chat.requests) == 2
    assert ledger.topics == []
    assert publisher.messages == []


def test_ledger_failure_does_not_block_publishing():
    chat = FakeChat(["今天也辛苦啦。"])
    pipeline, _, publisher = make_pipeline(chat, ledger=FakeLedger(fail=True))

    result = pipeline.handle(TRIGGER)

    assert result.sent is True
    assert len(publisher.messages) == 1


def test_publish_failure_is_reported_after_the_ledger_is_written():
    chat = FakeChat(["今天也辛苦啦。"])
    pipeline, ledger, _ = make_pipeline(chat, publisher=FakePublisher(fail=True))

    result = pipeline.handle(TRIGGER)

    assert result.sent is False
    assert result.reason == "ws_failed"
    assert result.message_id == "m1"
    assert ledger.topics == [("app_in_use.code", AT)]


def test_new_trigger_is_dropped_while_a_round_is_running():
    publisher = FakePublisher()
    pipeline: ProactivePipeline | None = None
    nested: list[ProactiveResult] = []

    class NestedChat:
        def chat(self, request: ChatRequest) -> ChatReply:
            assert pipeline is not None
            nested.append(pipeline.handle(Trigger("sit_long", "已连续坐着 60 分钟")))
            return ChatReply(message_id="m1", text="起来动动吧。", recorded=True)

    pipeline = ProactivePipeline(NestedChat(), FakeLedger(), publisher, clock=lambda: AT)

    result = pipeline.handle(TRIGGER)

    assert result.sent is True
    assert nested == [ProactiveResult(False, "sit_long", reason="pipeline_busy")]
    assert len(publisher.messages) == 1
