from datetime import datetime
from pathlib import Path

import pytest

from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import migrate


@pytest.fixture
def log(tmp_path: Path) -> DbDailyLog:
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    return DbDailyLog(db_file)


def test_append_writes_entries_in_order(log: DbDailyLog):
    log.append(
        "2026-10-25",
        "2026-10-25T09:00:00.000+08:00",
        [("user", "你好"), ("assistant", "你好呀")],
    )
    records = log.by_date("2026-10-25")
    assert [(record.role, record.content) for record in records] == [
        ("user", "你好"),
        ("assistant", "你好呀"),
    ]


def test_by_date_is_ascending_and_limited_to_the_day(log: DbDailyLog):
    log.append("2026-10-25", "2026-10-25T10:00:00.000+08:00", [("user", "后说")])
    log.append("2026-10-25", "2026-10-25T09:00:00.000+08:00", [("user", "先说")])
    log.append("2026-10-24", "2026-10-24T09:00:00.000+08:00", [("user", "昨天")])
    assert [record.content for record in log.by_date("2026-10-25")] == ["先说", "后说"]


def test_search_matches_keyword_and_date_range(log: DbDailyLog):
    log.append("2026-10-24", "2026-10-24T09:00:00.000+08:00", [("user", "喜欢美式咖啡")])
    log.append("2026-10-25", "2026-10-25T09:00:00.000+08:00", [("user", "喜欢茶")])
    log.append("2026-10-25", "2026-10-25T10:00:00.000+08:00", [("user", "100% 确定")])

    assert [r.content for r in log.search("咖啡")] == ["喜欢美式咖啡"]
    assert [r.content for r in log.search("喜欢", date_from="2026-10-25")] == ["喜欢茶"]
    assert [r.content for r in log.search("喜欢", date_to="2026-10-24")] == ["喜欢美式咖啡"]


def test_search_treats_wildcards_as_plain_text(log: DbDailyLog):
    log.append("2026-10-25", "2026-10-25T09:00:00.000+08:00", [("user", "100% 确定")])
    log.append("2026-10-25", "2026-10-25T10:00:00.000+08:00", [("user", "1000 元")])
    assert [r.content for r in log.search("100%")] == ["100% 确定"]
    assert [r.content for r in log.search("_")] == []


def test_timestamps_are_stored_with_timezone(log: DbDailyLog):
    log.append("2026-10-25", "2026-10-25T09:00:00.000+08:00", [("user", "x")])
    created_at = log.by_date("2026-10-25")[0].created_at
    assert datetime.fromisoformat(created_at).tzinfo is not None
