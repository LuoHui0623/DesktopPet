import json

import pytest

from service.memory.daily_events import (
    EventError,
    decode_event,
    display_content,
    encode_event,
    event_in_context,
    event_text,
)


def test_encode_writes_type_and_fields_as_json():
    content = encode_event("session_boundary", persona="小桌")
    assert json.loads(content) == {"type": "session_boundary", "persona": "小桌"}
    assert "小桌" in content


def test_encode_strips_values_and_decode_round_trips():
    content = encode_event("dispatch_failed", capability="  记忆检索 ", reason="超时")
    assert decode_event(content) == (
        "dispatch_failed",
        {"capability": "记忆检索", "reason": "超时"},
    )


def test_unknown_type_is_rejected():
    with pytest.raises(EventError, match="未知的 event 类型"):
        encode_event("weather", city="北京")


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"persona": "小桌", "extra": "x"},
        {"persona": ""},
        {"persona": "   "},
    ],
)
def test_invalid_fields_are_rejected(fields):
    with pytest.raises(EventError):
        encode_event("session_boundary", **fields)


def test_decode_rejects_non_json_and_missing_type():
    with pytest.raises(EventError, match="不是合法的 JSON"):
        decode_event("切换人设为小桌")
    with pytest.raises(EventError, match="缺少 type"):
        decode_event(json.dumps({"persona": "小桌"}))


def test_decode_rejects_wrong_fields_in_stored_content():
    with pytest.raises(EventError):
        decode_event(json.dumps({"type": "dispatch_failed", "capability": "x"}))


def test_session_boundary_is_kept_out_of_context_others_are_in():
    assert event_in_context(encode_event("session_boundary", persona="小桌")) is False
    assert event_in_context(encode_event("dispatch_failed", capability="a", reason="b")) is True
    assert event_in_context(encode_event("approval_rejected", capability="a", reason="b")) is True


def test_unparseable_event_is_not_sent_to_context_but_kept_as_text():
    assert event_in_context("旧的纯文本事件") is False
    assert event_text("旧的纯文本事件") == "旧的纯文本事件"


def test_event_text_is_rendered_from_its_template():
    assert event_text(encode_event("session_boundary", persona="小桌")) == (
        "切换人设为小桌，新会话开始"
    )
    assert event_text(
        encode_event("approval_rejected", capability="记忆写入", reason="未确认")
    ) == ("审批被拒绝：记忆写入，未确认")


def test_display_content_renders_events_and_leaves_turns_unchanged():
    content = encode_event("session_boundary", persona="小桌")
    assert display_content("event", content) == "切换人设为小桌，新会话开始"
    assert display_content("me", "你好呀") == "你好呀"
