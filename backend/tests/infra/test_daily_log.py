from datetime import datetime
from pathlib import Path

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from module.memory.contracts import DailyLogRecord, DailyLogRole
from module.memory.repository import DbDailyLog


@pytest.fixture
def log(tmp_path: Path) -> DbDailyLog:
    db_file = tmp_path / "pet.db"
    conn = connect(db_file)
    migrate(conn)
    conn.close()
    return DbDailyLog(db_file)


def entry(
    event_time: str,
    role: DailyLogRole,
    content: str,
    persona_id: str = "芙芙",
) -> DailyLogRecord:
    return DailyLogRecord(
        event_time=event_time,
        role=role,
        content=content,
        created_at=event_time,
        persona_id=persona_id,
    )


def test_append_writes_entries_in_order(log: DbDailyLog):
    log.append(
        [
            entry("2026-10-25T09:00:00.000+08:00", "master", "你好"),
            entry("2026-10-25T09:00:00.000+08:00", "me", "你好呀"),
        ]
    )
    records = log.by_date("2026-10-25")
    assert [(record.role, record.content) for record in records] == [
        ("master", "你好"),
        ("me", "你好呀"),
    ]


def test_by_date_is_ascending_and_limited_to_the_day(log: DbDailyLog):
    log.append([entry("2026-10-25T10:00:00.000+08:00", "master", "后说")])
    log.append([entry("2026-10-25T09:00:00.000+08:00", "master", "先说")])
    log.append([entry("2026-10-24T09:00:00.000+08:00", "master", "昨天")])
    assert [record.content for record in log.by_date("2026-10-25")] == ["先说", "后说"]


def test_search_matches_keyword_and_date_range(log: DbDailyLog):
    log.append([entry("2026-10-24T09:00:00.000+08:00", "master", "喜欢美式咖啡")])
    log.append([entry("2026-10-25T09:00:00.000+08:00", "master", "喜欢茶")])
    log.append([entry("2026-10-25T10:00:00.000+08:00", "master", "100% 确定")])

    assert [r.content for r in log.search("咖啡")] == ["喜欢美式咖啡"]
    assert [r.content for r in log.search("喜欢", date_from="2026-10-25")] == ["喜欢茶"]
    assert [r.content for r in log.search("喜欢", date_to="2026-10-24")] == ["喜欢美式咖啡"]


def test_search_treats_wildcards_as_plain_text(log: DbDailyLog):
    log.append([entry("2026-10-25T09:00:00.000+08:00", "master", "100% 确定")])
    log.append([entry("2026-10-25T10:00:00.000+08:00", "master", "1000 元")])
    assert [r.content for r in log.search("100%")] == ["100% 确定"]
    assert [r.content for r in log.search("_")] == []


def test_timestamps_are_stored_with_timezone(log: DbDailyLog):
    log.append([entry("2026-10-25T09:00:00.000+08:00", "master", "x")])
    record = log.by_date("2026-10-25")[0]
    assert datetime.fromisoformat(record.event_time).tzinfo is not None
    assert datetime.fromisoformat(record.created_at).tzinfo is not None


def test_by_date_can_be_limited_to_one_persona(log: DbDailyLog):
    log.append(
        [
            entry("2026-10-25T09:00:00.000+08:00", "master", "芙芙的话", "芙芙"),
            entry("2026-10-25T09:01:00.000+08:00", "master", "小桌的话", "小桌"),
        ]
    )
    assert [record.content for record in log.by_date("2026-10-25", persona_id="小桌")] == [
        "小桌的话"
    ]
    assert len(log.by_date("2026-10-25")) == 2


def test_assistant_records_are_stored_and_read(log: DbDailyLog):
    log.append(
        [
            entry("2026-10-25T09:00:00.000+08:00", "master", "查一下"),
            entry("2026-10-25T09:00:01.000+08:00", "assistant", "查到咖啡偏好"),
            entry("2026-10-25T09:00:02.000+08:00", "me", "记下啦"),
        ]
    )
    assert [(record.role, record.persona_id) for record in log.by_date("2026-10-25")] == [
        ("master", "芙芙"),
        ("assistant", "芙芙"),
        ("me", "芙芙"),
    ]


def test_recollection_records_are_stored_and_read(log: DbDailyLog):
    log.append([entry("2026-10-24T09:00:00.000+08:00", "回忆", "昨天聊了咖啡", "小桌")])
    [record] = log.by_date("2026-10-24", persona_id="小桌")
    assert (record.role, record.content, record.persona_id) == ("回忆", "昨天聊了咖啡", "小桌")
