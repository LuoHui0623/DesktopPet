import sqlite3
from pathlib import Path

import pytest

from constants.path import MIGRATIONS_DIR
from infra.store.db import connect, transaction
from infra.store.migrate import MigrationError, migrate, split_statements

EXPECTED_TABLES = {
    "daily_log",
    "llm_call",
    "agent_run",
    "persona_version",
    "behavior_event",
    "capability_run",
    "chat_import",
    "weather_cache",
    "app_setting",
    "log_record",
    "memory_structured",
    "schema_version",
}


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "pet.db")
    yield connection
    connection.close()


def tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


def write_migrations(directory: Path, files: dict[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, sql in files.items():
        (directory / name).write_text(sql, encoding="utf-8")
    return directory


def test_connect_creates_database_file_and_parent_dir(tmp_path: Path):
    target = tmp_path / "nested" / "pet.db"
    conn = connect(target)
    conn.close()
    assert target.exists()


def test_connect_enables_wal_and_foreign_keys(conn: sqlite3.Connection):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_first_migrate_creates_all_tables(conn: sqlite3.Connection):
    applied = migrate(conn)
    assert applied == [1, 2]
    assert EXPECTED_TABLES <= tables(conn)
    assert not {"core_memory", "proactive_feedback"} & tables(conn)


def test_second_migrate_is_a_no_op(conn: sqlite3.Connection):
    migrate(conn)
    assert migrate(conn) == []
    rows = conn.execute("SELECT version, name FROM schema_version").fetchall()
    assert [tuple(row) for row in rows] == [(1, "init"), (2, "memory_structured")]


def test_schema_version_records_checksum(conn: sqlite3.Connection):
    migrate(conn)
    checksum = conn.execute("SELECT checksum FROM schema_version WHERE version = 1").fetchone()[0]
    assert len(checksum) == 64


def test_foreign_key_is_enforced(conn: sqlite3.Connection):
    """建表迁移已无外键（core_memory、proactive_feedback 均移除），现建一张子表验证连接级开关。"""
    migrate(conn)
    conn.execute(
        "CREATE TABLE daily_ref (log_id INTEGER REFERENCES daily_log (id) ON DELETE CASCADE)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO daily_ref (log_id) VALUES (999)")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO llm_call (provider, model, success, created_at) VALUES ('p', 'm', 2, 'x')",
        "INSERT INTO agent_run (id, session_id, skill, instruction, status, started_at) "
        "VALUES ('r', 's', 'c', 'i', 'unknown', 'x')",
    ],
)
def test_check_constraints_are_enforced(conn: sqlite3.Connection, statement: str):
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(statement)


def test_transaction_rolls_back_on_error(conn: sqlite3.Connection):
    migrate(conn)
    with pytest.raises(RuntimeError):
        with transaction(conn):
            conn.execute("INSERT INTO app_setting (key, value, updated_at) VALUES ('a', '1', 'x')")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM app_setting").fetchone()[0] == 0


def test_failed_migration_is_rolled_back(tmp_path: Path, conn: sqlite3.Connection):
    directory = write_migrations(
        tmp_path / "migrations",
        {
            "0001_ok.sql": "CREATE TABLE a (id INTEGER PRIMARY KEY);",
            "0002_broken.sql": (
                "CREATE TABLE b (id INTEGER PRIMARY KEY);\nINSERT INTO missing VALUES (1);"
            ),
        },
    )
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, directory)
    assert "a" in tables(conn)
    assert "b" not in tables(conn)
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
    assert versions == [1]


def test_modified_applied_migration_is_rejected(tmp_path: Path, conn: sqlite3.Connection):
    directory = write_migrations(
        tmp_path / "migrations", {"0001_one.sql": "CREATE TABLE a (id INTEGER);"}
    )
    migrate(conn, directory)
    (directory / "0001_one.sql").write_text(
        "CREATE TABLE a (id INTEGER, x TEXT);", encoding="utf-8"
    )
    with pytest.raises(MigrationError, match="已被修改"):
        migrate(conn, directory)


def test_unknown_applied_version_is_rejected(tmp_path: Path, conn: sqlite3.Connection):
    directory = write_migrations(
        tmp_path / "migrations", {"0001_one.sql": "CREATE TABLE a (id INTEGER);"}
    )
    migrate(conn, directory)
    conn.execute(
        "INSERT INTO schema_version (version, name, checksum, applied_at) VALUES (9, 'x', 'x', 'x')"
    )
    with pytest.raises(MigrationError, match="未知迁移"):
        migrate(conn, directory)


def test_numbering_gap_is_rejected(tmp_path: Path, conn: sqlite3.Connection):
    directory = write_migrations(
        tmp_path / "migrations",
        {
            "0001_one.sql": "CREATE TABLE a (id INTEGER);",
            "0003_three.sql": "CREATE TABLE c (id INTEGER);",
        },
    )
    with pytest.raises(MigrationError, match="连续编号"):
        migrate(conn, directory)


def test_invalid_file_name_is_rejected(tmp_path: Path, conn: sqlite3.Connection):
    directory = write_migrations(
        tmp_path / "migrations", {"init.sql": "CREATE TABLE a (id INTEGER);"}
    )
    with pytest.raises(MigrationError, match="文件名不合法"):
        migrate(conn, directory)


def test_real_migrations_directory_is_valid():
    migrations_dir = MIGRATIONS_DIR
    assert sorted(path.name for path in migrations_dir.glob("*.sql")) == [
        "0001_init.sql",
        "0002_memory_structured.sql",
    ]


def test_split_statements_keeps_semicolons_inside_strings():
    sql = "CREATE TABLE a (x TEXT DEFAULT 'a;b');\n-- comment\nCREATE TABLE b (id INTEGER);\n"
    statements = split_statements(sql)
    assert len(statements) == 2
    assert "'a;b'" in statements[0]


def test_legacy_memory_tables_are_removed(conn: sqlite3.Connection):
    migrate(conn)
    for name in ("memory", "memory_archive", "daily_summary", "consolidation_run"):
        assert name not in tables(conn)


def test_core_memory_table_is_dropped(conn: sqlite3.Connection):
    migrate(conn)
    assert "core_memory" not in tables(conn)


def test_llm_call_has_agent_columns(conn: sqlite3.Connection):
    migrate(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_call)")}
    assert {
        "purpose",
        "run_id",
        "started_at",
        "finished_at",
        "usage_raw",
        "prompt_hash",
        "prompt_text",
        "reply_text",
        "reply_raw",
    } <= columns


def test_agent_run_columns(conn: sqlite3.Connection):
    migrate(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_run)")}
    assert columns == {
        "id",
        "session_id",
        "skill",
        "instruction",
        "steps",
        "output",
        "status",
        "error_code",
        "started_at",
        "finished_at",
        "turn_id",
        "persona_id",
        "persona_version",
        "main_task_id",
        "source",
    }


def test_migration_runs_on_empty_database(tmp_path: Path):
    conn = connect(tmp_path / "pet.db")
    try:
        assert migrate(conn) == [1, 2]
        assert migrate(conn) == []
    finally:
        conn.close()


def test_baseline_daily_log_uses_event_time(conn: sqlite3.Connection):
    """基线直接落最终结构：daily_log 只有 event_time，不再有历史 date 列。"""
    migrate(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_log)")}
    assert "event_time" in columns
    assert "date" not in columns
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(daily_log)")}
    assert indexes == {
        "idx_daily_log_event_time",
        "idx_daily_log_persona_event_time",
        "idx_daily_log_recollection",
    }


def insert_recollection(
    conn: sqlite3.Connection, moment: str, content: str, persona: str = "芙芙"
) -> None:
    conn.execute(
        "INSERT INTO daily_log (event_time, role, content, persona_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (moment, "回忆", content, persona, moment),
    )


def test_daily_log_accepts_recollection_role(conn: sqlite3.Connection):
    migrate(conn)
    insert_recollection(conn, "2026-10-25T09:00:00.000+08:00", "昨天聊了咖啡")
    rows = conn.execute("SELECT role, content FROM daily_log").fetchall()
    assert [tuple(row) for row in rows] == [("回忆", "昨天聊了咖啡")]


def test_recollection_is_unique_per_persona_and_day(conn: sqlite3.Connection):
    migrate(conn)
    insert_recollection(conn, "2026-10-25T09:00:00.000+08:00", "芙芙的回忆")
    insert_recollection(conn, "2026-10-25T09:30:00.000+08:00", "小桌的回忆", persona="小桌")
    insert_recollection(conn, "2026-10-26T09:00:00.000+08:00", "次日的回忆")
    with pytest.raises(sqlite3.IntegrityError):
        insert_recollection(conn, "2026-10-25T18:00:00.000+08:00", "重复的回忆")

    conn.execute(
        "INSERT INTO daily_log (event_time, role, content, persona_id, created_at) "
        "VALUES ('2026-10-25T18:00:00.000+08:00', 'me', '当天仍可多条聊天', '芙芙', "
        "'2026-10-25T18:00:00.000+08:00')"
    )
    roles = conn.execute("SELECT role FROM daily_log ORDER BY id").fetchall()
    assert [row[0] for row in roles] == ["回忆", "回忆", "回忆", "me"]


def test_repeated_migration_keeps_single_recollection_index(conn: sqlite3.Connection):
    migrate(conn)
    assert migrate(conn) == []
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(daily_log)")}
    assert "idx_daily_log_recollection" in indexes


def test_memory_structured_schema_and_constraints(conn: sqlite3.Connection):
    migrate(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_structured)")}
    assert columns == {
        "id",
        "content",
        "tags",
        "priority",
        "source_ref",
        "created_at",
        "updated_at",
        "status_state",
        "status_reason",
    }
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(memory_structured)")}
    assert {"idx_memory_structured_state", "idx_memory_structured_source_content"} <= indexes

    conn.execute(
        "INSERT INTO memory_structured (content, tags, priority, source_ref, created_at, "
        "updated_at) VALUES ('脆柿子', '[\"喜欢\",\"水果\"]', 2, 'structured.md:2', 't', 't')"
    )

    for content, tags, priority, status in (
        ("非数组", '"喜欢"', 2, "enabled"),
        ("对象", '{"a":1}', 2, "enabled"),
        ("null", "null", 2, "enabled"),
        ("非法", "not json", 2, "enabled"),
        ("越界", '["水果"]', 4, "enabled"),
        ("   ", '["水果"]', 2, "enabled"),
        ("未知状态", '["水果"]', 2, "deleted"),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory_structured (content, tags, priority, created_at, updated_at, "
                "status_state) VALUES (?, ?, ?, 't', 't', ?)",
                (content, tags, priority, status),
            )

    # 元素非字符串的数组仍可写入，由读取路径降级（R1 的已知边界）。
    conn.execute(
        "INSERT INTO memory_structured (content, tags, created_at, updated_at) "
        "VALUES ('数字标签', '[1]', 't', 't')"
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO memory_structured (content, tags, source_ref, created_at, updated_at) "
            "VALUES ('脆柿子', '[\"喜欢\"]', 'structured.md:2', 't', 't')"
        )

    # 运行时条目 source_ref 为空，不参与唯一约束。
    conn.execute(
        "INSERT INTO memory_structured (content, tags, created_at, updated_at) "
        "VALUES ('重复但无来源', '[\"喜欢\"]', 't', 't')"
    )
    conn.execute(
        "INSERT INTO memory_structured (content, tags, created_at, updated_at) "
        "VALUES ('重复但无来源', '[\"喜欢\"]', 't', 't')"
    )
