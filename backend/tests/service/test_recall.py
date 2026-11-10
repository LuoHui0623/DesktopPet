from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.memory.daily_log_service import DailyLogService
from module.memory.recall import (
    RECALL_HIT_MAX_CHARS,
    RECALL_MAX_QUERIES,
    RecallService,
    keywords,
)
from module.memory.repository import DbDailyLog
from module.settings.settings_service import SettingsService

TZ = ZoneInfo("Asia/Shanghai")
TODAY = date(2026, 11, 9)


@pytest.fixture
def daily(tmp_path: Path) -> DailyLogService:
    path = tmp_path / "pet.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return DailyLogService(
        DbDailyLog(path),
        SettingsService(path),
        TZ,
        clock=lambda tz: datetime(2026, 11, 9, 12, 0, tzinfo=TZ),
    )


def chat_at(daily: DailyLogService, moment: str, user: str, reply: str) -> None:
    at = datetime.fromisoformat(moment).replace(tzinfo=TZ)
    daily.record_turn(user, reply, received_at=at, replied_at=at)


def recall(daily, text: str):
    return RecallService(daily).recall(text, today=TODAY, persona_id=daily.active_persona())


def test_keywords_extract_short_cjk_units():
    words = keywords("还记得冰汽时代吗")
    assert "冰汽" in words
    assert "时代" in words
    assert len(words) <= RECALL_MAX_QUERIES


def test_keywords_skip_greetings_and_filler():
    assert keywords("你好呀") == ()
    assert keywords("今天怎么样") == ()


def test_recall_uses_earlier_chat_lines_only(daily: DailyLogService):
    chat_at(daily, "2026-11-07T20:00:00", "今天玩了冰汽时代", "冰汽时代太难了")
    daily.record_assistant("检索结果：冰汽时代资料", at=datetime(2026, 11, 7, 21, 0, tzinfo=TZ))
    daily.record_recollection("2026-11-07", "冰汽时代的回忆")
    chat_at(daily, "2026-11-09T11:00:00", "今天又玩冰汽时代", "又死档了")

    hits = recall(daily, "还记得冰汽时代吗")
    texts = [hit.text for hit in hits]

    assert "冰汽时代太难了" in texts
    assert all(hit.role in {"master", "me"} for hit in hits)
    assert all("今天又玩" not in text for text in texts)
    assert all("检索结果" not in text for text in texts)
    assert all("回忆" not in text for text in texts)


def test_recall_dedupes_lines_matched_by_several_keywords(daily: DailyLogService):
    chat_at(daily, "2026-11-07T20:00:00", "冰汽时代", "冰汽时代太难了")

    hits = recall(daily, "冰汽时代 冰汽时代")

    matched = [hit for hit in hits if hit.text == "冰汽时代太难了"]
    assert len(matched) == 1
    assert len({hit.id for hit in hits}) == len(hits)


def test_recall_returns_newest_lines_first(daily: DailyLogService):
    chat_at(daily, "2026-11-06T20:00:00", "旧的冰汽时代", "旧的冰汽时代记录")
    chat_at(daily, "2026-11-08T20:00:00", "新的冰汽时代", "新的冰汽时代记录")

    hits = recall(daily, "冰汽时代")

    days = [hit.day for hit in hits]
    assert days == sorted(days, reverse=True)
    assert hits[0].day == "2026-11-08"


def test_recall_prefers_lines_matching_more_keywords(daily: DailyLogService):
    chat_at(daily, "2026-11-07T20:00:00", "冰汽时代", "冰汽时代太难了")
    chat_at(daily, "2026-11-08T20:00:00", "时代变了", "时代真的变了")

    hits = recall(daily, "冰汽时代")

    assert hits[0].text == "冰汽时代太难了"


def test_recall_clips_long_lines(daily: DailyLogService):
    chat_at(daily, "2026-11-07T20:00:00", "冰汽时代" + "字" * 300, "冰汽时代" + "字" * 300)

    hits = recall(daily, "冰汽时代")

    assert hits
    assert all(len(hit.text) <= RECALL_HIT_MAX_CHARS for hit in hits)


def test_recall_without_keywords_does_not_touch_storage():
    class CountingDaily:
        calls = 0

        def search(self, *args, **kwargs):
            self.calls += 1
            return []

    counting = CountingDaily()
    service = RecallService(counting)  # type: ignore[arg-type]

    assert service.recall("你好呀", today=TODAY, persona_id="芙芙") == []
    assert counting.calls == 0


def test_recall_swallows_search_failures():
    class BrokenDaily:
        def search(self, *args, **kwargs):
            raise RuntimeError("库坏了")

    service = RecallService(BrokenDaily())  # type: ignore[arg-type]

    assert service.recall("冰汽时代", today=TODAY, persona_id="芙芙") == []
