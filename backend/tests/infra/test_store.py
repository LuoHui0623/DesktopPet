import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from constants.path import MIGRATIONS_DIR
from infra.store.db import connect, transaction
from infra.store.migrate import MigrationError, migrate, split_statements

EXPECTED_TABLES = {
    "daily_log",
    "llm_usage",
    "agent_run",
    "persona_version",
    "behavior_event",
    "capability_run",
    "chat_import",
    "weather_cache",
    "app_setting",
    "log_record",
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
    assert applied == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert EXPECTED_TABLES <= tables(conn)
    assert not {"core_memory", "proactive_feedback"} & tables(conn)


def test_second_migrate_is_a_no_op(conn: sqlite3.Connection):
    migrate(conn)
    assert migrate(conn) == []
    rows = conn.execute("SELECT version, name FROM schema_version").fetchall()
    assert [tuple(row) for row in rows] == [
        (1, "init"),
        (2, "log_record"),
        (3, "agent"),
        (4, "persona_card"),
        (5, "daily_log_roles"),
        (6, "daily_event_structure"),
        (7, "agent_run_rejected"),
        (8, "drop_core_memory"),
        (9, "drop_proactive_feedback"),
    ]


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
        "INSERT INTO llm_usage (provider, model, success, created_at) VALUES ('p', 'm', 2, 'x')",
        "INSERT INTO agent_run (id, session_id, capability, instruction, status, started_at) "
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
    assert (migrations_dir / "0001_init.sql").exists()


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


def test_llm_usage_has_agent_columns(conn: sqlite3.Connection):
    migrate(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_usage)")}
    assert {
        "purpose",
        "run_id",
        "started_at",
        "finished_at",
        "usage_raw",
        "prompt_hash",
    } <= columns


def test_agent_run_columns(conn: sqlite3.Connection):
    migrate(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_run)")}
    assert columns == {
        "id",
        "session_id",
        "capability",
        "instruction",
        "steps",
        "output",
        "status",
        "error_code",
        "started_at",
        "finished_at",
    }


def test_agent_run_accepts_rejected_status(tmp_path: Path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    for name in (
        "0001_init.sql",
        "0002_log_record.sql",
        "0003_agent.sql",
        "0004_persona_card.sql",
        "0005_daily_log_roles.sql",
        "0006_daily_event_structure.sql",
    ):
        shutil.copy(MIGRATIONS_DIR / name, old_dir / name)

    conn = connect(tmp_path / "pet.db")
    try:
        assert migrate(conn, old_dir) == [1, 2, 3, 4, 5, 6]
        insert = (
            "INSERT INTO agent_run (id, session_id, capability, instruction, status, started_at) "
            "VALUES (?, 's', 'c', 'i', ?, 'x')"
        )
        conn.execute(insert, ("r", "failed"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("r2", "rejected"))

        assert migrate(conn) == [7, 8, 9]

        conn.execute(insert, ("r2", "rejected"))
        rows = conn.execute("SELECT id, status, steps FROM agent_run ORDER BY id").fetchall()
        assert [tuple(row) for row in rows] == [("r", "failed", "[]"), ("r2", "rejected", "[]")]
    finally:
        conn.close()


def test_migration_upgrades_existing_database_and_keeps_usage_rows(tmp_path: Path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    for name in ("0001_init.sql", "0002_log_record.sql"):
        shutil.copy(MIGRATIONS_DIR / name, old_dir / name)

    conn = connect(tmp_path / "pet.db")
    try:
        assert migrate(conn, old_dir) == [1, 2]
        conn.execute(
            "INSERT INTO llm_usage (provider, model, success, created_at) "
            "VALUES ('deepseek', 'deepseek-flash', 1, '2026-10-25T10:00:00+08:00')"
        )
        conn.execute("INSERT INTO daily_summary (date, summary) VALUES ('2026-10-24', 'x')")
        conn.execute(
            "INSERT INTO memory (category, content, explicit, valid_from, updated_at) "
            "VALUES ('preference', 'x', 1, '2026-10-24', 'x')"
        )
        conn.execute("INSERT INTO core_memory (memory_id, sort_order) VALUES ('1', 1)")

        assert migrate(conn) == [3, 4, 5, 6, 7, 8, 9]

        usage = conn.execute(
            "SELECT provider, model, success, purpose, run_id, usage_raw FROM llm_usage"
        ).fetchone()
        assert tuple(usage) == ("deepseek", "deepseek-flash", 1, None, None, None)
        assert "memory" not in tables(conn)
        assert "daily_summary" not in tables(conn)
        assert "core_memory" not in tables(conn)
        assert "proactive_feedback" not in tables(conn)
        versions = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
        assert [tuple(row) for row in versions] == [
            (1,),
            (2,),
            (3,),
            (4,),
            (5,),
            (6,),
            (7,),
            (8,),
            (9,),
        ]
    finally:
        conn.close()


def test_migration_runs_on_empty_database(tmp_path: Path):
    conn = connect(tmp_path / "pet.db")
    try:
        assert migrate(conn) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
        assert migrate(conn) == []
    finally:
        conn.close()


def test_daily_log_roles_are_renamed_and_old_names_rejected(tmp_path: Path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    for name in ("0001_init.sql", "0002_log_record.sql", "0003_agent.sql", "0004_persona_card.sql"):
        shutil.copy(MIGRATIONS_DIR / name, old_dir / name)

    conn = connect(tmp_path / "pet.db")
    try:
        assert migrate(conn, old_dir) == [1, 2, 3, 4]
        conn.execute(
            "INSERT INTO daily_log (date, role, content, created_at) VALUES "
            "('2026-10-25', 'user', '你好', '2026-10-25T09:00:00+08:00'), "
            "('2026-10-25', 'assistant', '你好呀', '2026-10-25T09:00:01+08:00'), "
            "('2026-10-25', 'event', '切换人设', '2026-10-25T09:05:00+08:00')"
        )

        assert migrate(conn) == [5, 6, 7, 8, 9]

        rows = conn.execute("SELECT id, role, content FROM daily_log ORDER BY id").fetchall()
        assert [tuple(row) for row in rows] == [
            (1, "master", "你好"),
            (2, "me", "你好呀"),
            (3, "event", "切换人设"),
        ]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO daily_log (date, role, content, created_at) "
                "VALUES ('2026-10-25', 'user', 'x', 'x')"
            )
    finally:
        conn.close()


def test_session_boundary_text_becomes_structured_event(tmp_path: Path):
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    for name in (
        "0001_init.sql",
        "0002_log_record.sql",
        "0003_agent.sql",
        "0004_persona_card.sql",
        "0005_daily_log_roles.sql",
    ):
        shutil.copy(MIGRATIONS_DIR / name, old_dir / name)

    conn = connect(tmp_path / "pet.db")
    try:
        assert migrate(conn, old_dir) == [1, 2, 3, 4, 5]
        conn.execute(
            "INSERT INTO daily_log (date, role, content, created_at) VALUES "
            "('2026-10-25', 'event', '切换人设为 小桌，新会话开始', 'x'), "
            "('2026-10-25', 'event', '其他旧事件', 'y')"
        )

        assert migrate(conn) == [6, 7, 8, 9]

        rows = conn.execute("SELECT content FROM daily_log ORDER BY id").fetchall()
        assert json.loads(rows[0][0]) == {"type": "session_boundary", "persona": "小桌"}
        assert rows[1][0] == "其他旧事件"
    finally:
        conn.close()
