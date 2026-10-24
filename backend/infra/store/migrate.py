import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from constants.path import MIGRATIONS_DIR
from infra.store.db import transaction

MIGRATION_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_FILE.match(path.name)
        if match is None:
            raise MigrationError(f"迁移文件名不合法：{path.name}")
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(Migration(int(match.group(1)), match.group(2), sql, checksum))

    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError(f"迁移版本必须从 0001 开始连续编号，当前为 {versions}")
    return migrations


def split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    leftover = [
        line for line in buffer.splitlines() if line.strip() and not line.strip().startswith("--")
    ]
    if leftover:
        raise MigrationError("迁移文件末尾存在未结束的语句")
    return statements


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[int]:
    migrations = load_migrations(directory)
    conn.execute(SCHEMA_VERSION_TABLE)

    applied = {
        row[0]: row[1] for row in conn.execute("SELECT version, checksum FROM schema_version")
    }
    known = {migration.version: migration for migration in migrations}
    for version, checksum in applied.items():
        if version not in known:
            raise MigrationError(f"数据库包含未知迁移 {version:04d}，代码版本可能低于数据库版本")
        if known[version].checksum != checksum:
            raise MigrationError(
                f"迁移 {version:04d} 已应用，但文件已被修改。请新增迁移，不要修改已应用的迁移"
            )

    ran: list[int] = []
    for migration in migrations:
        if migration.version in applied:
            continue
        _apply(conn, migration)
        ran.append(migration.version)
    return ran


def _apply(conn: sqlite3.Connection, migration: Migration) -> None:
    applied_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with transaction(conn):
        for statement in split_statements(migration.sql):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_version (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
            (migration.version, migration.name, migration.checksum, applied_at),
        )
