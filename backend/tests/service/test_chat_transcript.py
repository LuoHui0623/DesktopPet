from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage

from module.chat.transcript import ChatLine, ChatTranscript

TZ = ZoneInfo("Asia/Shanghai")


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self, zone: ZoneInfo) -> datetime:
        return self.moment.astimezone(zone)


def line(
    moment: datetime,
    role: str,
    text: str,
    *,
    is_reply: bool = False,
    record_id: int | None = None,
) -> ChatLine:
    message = HumanMessage(content=text) if role == "master" else AIMessage(content=text)
    return ChatLine(
        at=moment,
        message=message,
        is_reply=is_reply,
        role=role,
        record_id=record_id,
        record_day=moment.date(),
    )


class Loader:
    def __init__(self, rows: dict[tuple[date, str], list[ChatLine]] | None = None) -> None:
        self.rows = rows or {}
        self.calls: list[tuple[date, str]] = []

    def __call__(self, day: date, persona_id: str) -> list[ChatLine]:
        self.calls.append((day, persona_id))
        return list(self.rows.get((day, persona_id), []))


def test_restored_lines_are_returned_in_source_order():
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    loader = Loader(
        {
            (moment.date(), "小桌"): [
                line(moment, "master", "你好", record_id=1),
                line(moment + timedelta(seconds=1), "assistant", "查到咖啡", record_id=2),
                line(
                    moment + timedelta(seconds=2),
                    "me",
                    "记住啦",
                    is_reply=True,
                    record_id=3,
                ),
            ]
        }
    )
    snapshot = ChatTranscript(TZ, clock=Clock(moment + timedelta(hours=1)), loader=loader)
    snapshot.set_persona("小桌")

    lines = snapshot.lines()

    assert [item.role for item in lines] == ["master", "assistant", "me"]
    assert [item.message.content for item in lines] == ["你好", "查到咖啡", "记住啦"]
    assert [item.is_reply for item in lines] == [False, False, True]
    assert [item.record_id for item in lines] == [1, 2, 3]
    assert loader.calls == [(moment.date(), "小桌")]


def test_same_moment_is_ordered_by_daily_log_identity():
    """同一时刻的多行以 daily_log 自增 id 作次级键（D-18）。"""
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    loader = Loader(
        {
            (moment.date(), "芙芙"): [
                line(moment, "me", "后写先给", is_reply=True, record_id=9),
                line(moment, "master", "先写后给", record_id=4),
            ]
        }
    )
    snapshot = ChatTranscript(TZ, clock=Clock(moment), loader=loader)

    assert [item.record_id for item in snapshot.lines()] == [4, 9]


def test_reading_uses_the_given_day_instead_of_the_clock():
    """整轮固定 received_at 日期：调用方给哪天就读哪天（D-07）。"""
    moment = datetime(2026, 10, 18, 23, 59, tzinfo=TZ)
    loader = Loader(
        {
            (date(2026, 10, 18), "芙芙"): [line(moment, "master", "昨晚的话", record_id=1)],
            (date(2026, 10, 19), "芙芙"): [
                line(moment + timedelta(minutes=2), "master", "今天的话", record_id=2)
            ],
        }
    )
    snapshot = ChatTranscript(
        TZ,
        clock=Clock(datetime(2026, 10, 19, 8, 0, tzinfo=TZ)),
        loader=loader,
    )

    assert [item.message.content for item in snapshot.lines(date(2026, 10, 18))] == ["昨晚的话"]
    assert loader.calls[-1] == (date(2026, 10, 18), "芙芙")


def test_several_utterances_are_restored_as_separate_lines():
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    loader = Loader(
        {
            (moment.date(), "芙芙"): [
                line(moment, "master", "你好", record_id=1),
                line(moment + timedelta(seconds=1), "me", "第一句。", is_reply=True, record_id=2),
                line(moment + timedelta(seconds=2), "me", "第二句！", is_reply=True, record_id=3),
            ]
        }
    )
    snapshot = ChatTranscript(TZ, clock=Clock(moment + timedelta(hours=1)), loader=loader)

    lines = snapshot.lines()

    assert len(lines) == 3
    assert [item.message.content for item in lines] == ["你好", "第一句。", "第二句！"]
    assert all(not item.message.content.startswith("{") for item in lines)


def test_persona_switch_reloads_from_the_new_persona():
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    loader = Loader(
        {
            (moment.date(), "芙芙"): [line(moment, "master", "芙芙在", record_id=1)],
            (moment.date(), "小桌"): [line(moment, "master", "小桌在", record_id=2)],
        }
    )
    snapshot = ChatTranscript(TZ, clock=Clock(moment + timedelta(hours=1)), loader=loader)
    assert [item.message.content for item in snapshot.lines()] == ["芙芙在"]

    snapshot.set_persona("小桌")

    assert [item.message.content for item in snapshot.lines()] == ["小桌在"]
    assert loader.calls[-1] == (moment.date(), "小桌")


def test_without_a_loader_only_the_requested_day_is_empty():
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    snapshot = ChatTranscript(TZ, clock=Clock(moment))
    assert snapshot.lines() == ()
