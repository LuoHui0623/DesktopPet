import sqlite3
from pathlib import Path

import pytest

from constants.path import MIGRATIONS_DIR
from infra.store.db import connect, transaction
from infra.store.migrate import MigrationError, migrate, split_statements

EXPECTED_TABLES = {
    "daily_log",
    "daily_summary",
    "memory",
    "memory_archive",
    "core_memory",
    "consolidation_run",
    "llm_usage",
    "persona_version",
    "behavior_event",
    "proactive_feedback",
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
    assert applied == [1, 2]
    assert EXPECTED_TABLES <= tables(conn)


def test_second_migrate_is_a_no_op(conn: sqlite3.Connection):
    migrate(conn)
    assert migrate(conn) == []
    rows = conn.execute("SELECT version, name FROM schema_version").fetchall()
    assert [tuple(row) for row in rows] == [(1, "init"), (2, "log_record")]


def test_schema_version_records_checksum(conn: sqlite3.Connection):
    migrate(conn)
    checksum = conn.execute("SELECT checksum FROM schema_version WHERE version = 1").fetchone()[0]
    assert len(checksum) == 64


def test_foreign_key_is_enforced(conn: sqlite3.Connection):
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO proactive_feedback (message_id, type, created_at) "
            "VALUES (999, 'like', 'x')"
        )


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO memory (category, content, explicit, valid_from, updated_at) "
        "VALUES ('unknown', 'x', 1, '2026-10-24', 'x')",
        "INSERT INTO memory (category, content, explicit, importance, valid_from, updated_at) "
        "VALUES ('preference', 'x', 1, 9, '2026-10-24', 'x')",
        "INSERT INTO llm_usage (provider, model, success, created_at) VALUES ('p', 'm', 2, 'x')",
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
