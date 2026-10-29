from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage

from module.chat.transcript import ChatLine, ChatTranscript

TZ = ZoneInfo("Asia/Shanghai")


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


def test_turn_is_stored_in_construction_order():
    snapshot = ChatTranscript(TZ, clock=Clock(datetime(2026, 10, 18, 9, 0, tzinfo=TZ)))

    snapshot.append_turn("你好", "你好呀")

    lines = snapshot.lines()
    assert [type(line.message) for line in lines] == [HumanMessage, AIMessage]
    assert [line.message.content for line in lines] == [
        "你好",
        '{"type": "chat", "text": "你好呀"}',
    ]
    assert [line.is_reply for line in lines] == [False, True]


def test_added_lines_keep_their_order_and_reply_flag():
    snapshot = ChatTranscript(TZ, clock=Clock(datetime(2026, 10, 18, 9, 0, tzinfo=TZ)))
    note = ChatLine(
        at=datetime(2026, 10, 18, 8, 55, tzinfo=TZ),
        message=AIMessage(content="[感知 08:55] 打开 vscode"),
    )

    snapshot.add(note)
    snapshot.append_turn("早", "早呀")

    lines = snapshot.lines()
    assert [line.message.content for line in lines] == [
        "[感知 08:55] 打开 vscode",
        "早",
        '{"type": "chat", "text": "早呀"}',
    ]
    assert [line.is_reply for line in lines] == [False, False, True]


def test_proactive_reply_is_appended_without_a_user_line():
    snapshot = ChatTranscript(TZ, clock=Clock(datetime(2026, 10, 18, 9, 0, tzinfo=TZ)))

    snapshot.append_reply("今天也辛苦啦")

    lines = snapshot.lines()
    assert [type(line.message) for line in lines] == [AIMessage]
    assert lines[0].message.content == '{"type": "chat", "text": "今天也辛苦啦"}'
    assert lines[0].is_reply is True


def test_day_change_clears_the_snapshot():
    clock = Clock(datetime(2026, 10, 18, 23, 0, tzinfo=TZ))
    snapshot = ChatTranscript(TZ, clock=clock)
    snapshot.append_turn("晚安", "晚安")

    clock.moment = datetime(2026, 10, 19, 8, 0, tzinfo=TZ)

    assert snapshot.lines() == ()
    snapshot.append_turn("早", "早呀")
    assert [line.message.content for line in snapshot.lines()] == [
        "早",
        '{"type": "chat", "text": "早呀"}',
    ]
