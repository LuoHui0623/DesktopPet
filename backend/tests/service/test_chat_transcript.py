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
) -> ChatLine:
    message = HumanMessage(content=text) if role == "master" else AIMessage(content=text)
    return ChatLine(at=moment, message=message, is_reply=is_reply, role=role)


def note(moment: datetime, text: str) -> ChatLine:
    return ChatLine(at=moment, message=AIMessage(content=text))


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
                line(moment, "master", "你好"),
                line(moment + timedelta(seconds=1), "assistant", "查到咖啡"),
                line(moment + timedelta(seconds=2), "me", "记住啦", is_reply=True),
            ]
        }
    )
    snapshot = ChatTranscript(TZ, clock=Clock(moment + timedelta(hours=1)), loader=loader)
    snapshot.set_persona("小桌")

    lines = snapshot.lines()

    assert [item.role for item in lines] == ["master", "assistant", "me"]
    assert [item.message.content for item in lines] == ["你好", "查到咖啡", "记住啦"]
    assert [item.is_reply for item in lines] == [False, False, True]
    assert loader.calls == [(moment.date(), "小桌")]


def test_several_utterances_are_restored_as_separate_lines():
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    loader = Loader(
        {
            (moment.date(), "芙芙"): [
                line(moment, "master", "你好"),
                line(moment + timedelta(seconds=1), "me", "第一句。", is_reply=True),
                line(moment + timedelta(seconds=2), "me", "第二句！", is_reply=True),
            ]
        }
    )
    snapshot = ChatTranscript(TZ, clock=Clock(moment + timedelta(hours=1)), loader=loader)

    lines = snapshot.lines()

    assert len(lines) == 3
    assert [item.message.content for item in lines] == ["你好", "第一句。", "第二句！"]
    assert all(not item.message.content.startswith("{") for item in lines)


def test_session_notes_are_merged_by_time_and_never_persisted():
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    loader = Loader(
        {
            (moment.date(), "芙芙"): [
                line(moment, "master", "你好"),
                line(moment + timedelta(seconds=5), "me", "早呀", is_reply=True),
            ]
        }
    )
    snapshot = ChatTranscript(TZ, clock=Clock(moment + timedelta(hours=1)), loader=loader)
    snapshot.add_note(note(moment + timedelta(seconds=3), "[感知 09:00] 打开 code"))

    lines = snapshot.lines()

    assert [item.message.content for item in lines] == [
        "你好",
        "[感知 09:00] 打开 code",
        "早呀",
    ]
    assert lines[1].role is None


def test_day_change_clears_notes_and_reloads_the_new_day():
    clock = Clock(datetime(2026, 10, 18, 23, 0, tzinfo=TZ))
    loader = Loader(
        {
            (date(2026, 10, 18), "芙芙"): [
                line(datetime(2026, 10, 18, 23, 0, tzinfo=TZ), "master", "昨天的话")
            ],
            (date(2026, 10, 19), "芙芙"): [
                line(datetime(2026, 10, 19, 8, 0, tzinfo=TZ), "master", "今天的话")
            ],
        }
    )
    snapshot = ChatTranscript(TZ, clock=clock, loader=loader)
    snapshot.add_note(note(datetime(2026, 10, 18, 23, 0, tzinfo=TZ), "[感知] 昨晚"))
    assert [item.message.content for item in snapshot.lines()] == ["昨天的话", "[感知] 昨晚"]

    clock.moment = datetime(2026, 10, 19, 8, 0, tzinfo=TZ)

    assert [item.message.content for item in snapshot.lines()] == ["今天的话"]
    assert loader.calls[-1] == (date(2026, 10, 19), "芙芙")


def test_persona_switch_reloads_and_discards_notes_of_the_previous_persona():
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    loader = Loader(
        {
            (moment.date(), "芙芙"): [line(moment, "master", "芙芙在")],
            (moment.date(), "小桌"): [line(moment, "master", "小桌在")],
        }
    )
    snapshot = ChatTranscript(TZ, clock=Clock(moment + timedelta(hours=1)), loader=loader)
    snapshot.add_note(note(moment, "[感知] 旧人设的会话"))
    assert [item.message.content for item in snapshot.lines()] == [
        "芙芙在",
        "[感知] 旧人设的会话",
    ]

    snapshot.set_persona("小桌")

    assert [item.message.content for item in snapshot.lines()] == ["小桌在"]
    assert loader.calls[-1] == (moment.date(), "小桌")


def test_without_a_loader_only_session_notes_are_returned():
    moment = datetime(2026, 10, 18, 9, 0, tzinfo=TZ)
    snapshot = ChatTranscript(TZ, clock=Clock(moment))
    snapshot.add_note(note(moment, "[事件] 派发失败"))
    assert [item.message.content for item in snapshot.lines()] == ["[事件] 派发失败"]
