import sqlite3
from pathlib import Path

import pytest

from scripts import squash_migrations

OLD_SCHEMA = """
CREATE TABLE daily_log (
    id INTEGER PRIMARY KEY,
    event_time TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_daily_log_event_time ON daily_log (event_time);
CREATE TABLE app_setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def make_old_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(OLD_SCHEMA)
        conn.execute(
            "INSERT INTO daily_log (event_time, role, content, persona_id, created_at) "
            "VALUES ('2026-11-01T09:00:00+08:00', 'master', '你好', '芙芙', "
            "'2026-11-01T09:00:00+08:00')"
        )
        conn.execute(
            "INSERT INTO app_setting (key, value, updated_at) "
            "VALUES ('persona.active', '芙芙', 'x')"
        )
        conn.execute(
            "CREATE TABLE schema_version ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO schema_version VALUES (?, ?, ?, ?)",
            [(version, f"old_{version}", "checksum", "x") for version in range(1, 18)],
        )
        conn.commit()
    finally:
        conn.close()


def make_old_migrations(path: Path) -> None:
    path.mkdir()
    for version in range(1, 18):
        (path / f"{version:04d}_old_{version}.sql").write_text(
            f"CREATE TABLE old_{version} (id INTEGER);\n", encoding="utf-8"
        )


def test_dump_schema_orders_tables_before_indexes_and_skips_schema_version(tmp_path: Path):
    db_file = tmp_path / "pet.db"
    make_old_database(db_file)
    conn = sqlite3.connect(db_file)
    try:
        schema = squash_migrations.dump_schema(conn)
    finally:
        conn.close()

    assert "CREATE TABLE daily_log" in schema
    assert "CREATE INDEX idx_daily_log_event_time" in schema
    assert schema.index("CREATE TABLE daily_log") < schema.index(
        "CREATE INDEX idx_daily_log_event_time"
    )
    assert "CREATE TABLE schema_version" not in schema
    assert "sqlite_" not in schema
    assert schema.endswith(";\n")


def test_squash_rebuilds_database_and_preserves_rows(tmp_path: Path):
    db_file = tmp_path / "data" / "pet.db"
    db_file.parent.mkdir()
    make_old_database(db_file)
    migrations = tmp_path / "migrations"
    make_old_migrations(migrations)

    report = squash_migrations.squash(db_file, migrations, apply=True)

    assert report.tables == {"daily_log": 1, "app_setting": 1}
    assert report.backup.exists()

    backup = sqlite3.connect(report.backup)
    try:
        assert backup.execute("SELECT COUNT(*) FROM daily_log").fetchone()[0] == 1
        assert backup.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 17
    finally:
        backup.close()

    conn = sqlite3.connect(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM daily_log").fetchone()[0] == 1
        assert conn.execute("SELECT content FROM daily_log").fetchone()[0] == "你好"
        assert conn.execute("SELECT value FROM app_setting").fetchone()[0] == "芙芙"
        assert conn.execute("SELECT version, name FROM schema_version").fetchall() == [(1, "init")]
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(daily_log)")}
        assert "idx_daily_log_event_time" in indexes
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()

    remaining = sorted(path.name for path in migrations.glob("*.sql"))
    assert remaining == ["0001_init.sql"]


def test_squash_failure_keeps_original_database_and_migrations(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "data" / "pet.db"
    db_file.parent.mkdir()
    make_old_database(db_file)
    migrations = tmp_path / "migrations"
    make_old_migrations(migrations)

    def boom(*args, **kwargs):
        raise RuntimeError("copy failed")

    monkeypatch.setattr(squash_migrations, "_copy_tables", boom)

    with pytest.raises(RuntimeError, match="copy failed"):
        squash_migrations.squash(db_file, migrations, apply=True)

    conn = sqlite3.connect(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM daily_log").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 17
    finally:
        conn.close()

    assert (migrations / "0001_old_1.sql").exists()
    assert not (migrations / "0001_init.sql").exists()
