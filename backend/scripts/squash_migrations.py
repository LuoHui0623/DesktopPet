"""一次性把 0001–0017 压成 0001 基线，并保留现有数据。

用法（执行前必须停止后端）：
    cd backend && uv run python -m scripts.squash_migrations
    cd backend && uv run python -m scripts.squash_migrations --apply

不带 --apply 时只演练：生成基线、在临时库上重建并校验，不改动真实库与迁移目录。
"""

import argparse
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from constants.path import DATA_DIR, MIGRATIONS_DIR
from infra.store.migrate import migrate

BASELINE_NAME = "0001_init.sql"
BASELINE_VERSION = 1
SYSTEM_TABLES = frozenset(("schema_version", "sqlite_sequence"))


@dataclass(frozen=True)
class SquashReport:
    backup: Path
    tables: dict[str, int]
    baseline: str
    new_db: Path
    applied: bool


def dump_schema(conn: sqlite3.Connection) -> str:
    """导出当前库的建表与索引语句，排除 schema_version 与 sqlite_* 内部对象。"""
    rows = conn.execute(
        """
        SELECT type, name, sql FROM sqlite_master
        WHERE sql IS NOT NULL
          AND name NOT LIKE 'sqlite_%'
          AND name <> 'schema_version'
        ORDER BY CASE type
                   WHEN 'table' THEN 0
                   WHEN 'index' THEN 1
                   WHEN 'trigger' THEN 2
                   ELSE 3
                 END,
                 name
        """
    ).fetchall()
    statements = [sql.strip().rstrip(";") + ";" for _type, _name, sql in rows]
    header = (
        "-- 基线：由 0001–0017 压缩生成，此后只增不改。\n"
        "-- 生成方式：scripts/squash_migrations.py（不含 schema_version）。\n"
    )
    return header + "\n".join(statements) + "\n"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name <> 'schema_version' "
        "ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({_quote(table)})")]


def _copy_tables(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    """conn 已 ATTACH 旧库为 old；逐表按列复制，返回写入行数。"""
    counts: dict[str, int] = {}
    for table in tables:
        old_columns = _columns(conn, "old", table)
        new_columns = _columns(conn, "main", table)
        if old_columns != new_columns:
            raise RuntimeError(f"表 {table} 的列不一致：旧库 {old_columns}，新库 {new_columns}")
        columns = ", ".join(_quote(column) for column in old_columns)
        conn.execute(
            f"INSERT INTO main.{_quote(table)} ({columns}) "
            f"SELECT {columns} FROM old.{_quote(table)}"
        )
        counts[table] = int(
            conn.execute(f"SELECT COUNT(*) FROM main.{_quote(table)}").fetchone()[0]
        )
    return counts


def _verify(conn: sqlite3.Connection, expected: dict[str, int]) -> None:
    for table, count in expected.items():
        actual = int(conn.execute(f"SELECT COUNT(*) FROM main.{_quote(table)}").fetchone()[0])
        if actual != count:
            raise RuntimeError(f"表 {table} 行数不一致：旧库 {count}，新库 {actual}")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"新库完整性校验失败：{integrity}")
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise RuntimeError(f"新库外键校验失败：{foreign_keys[:5]}")
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_version")]
    if versions != [BASELINE_VERSION]:
        raise RuntimeError(f"新库 schema_version 应为 [{BASELINE_VERSION}]，实际 {versions}")


def _open_build(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def _checkpoint(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass


def squash(db_file: Path, migrations_dir: Path, *, apply: bool) -> SquashReport:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_file.with_name(f"{db_file.name}.bak-{stamp}")
    new_db = db_file.with_name(f"{db_file.name}.new-{stamp}")
    migrations_new = migrations_dir.with_name(f"{migrations_dir.name}.new-{stamp}")
    migrations_old = migrations_dir.with_name(f"{migrations_dir.name}.old-{stamp}")

    old = sqlite3.connect(db_file, isolation_level=None)
    old.row_factory = sqlite3.Row
    try:
        try:
            old.execute("BEGIN IMMEDIATE")
            old.execute("ROLLBACK")
        except sqlite3.OperationalError as exc:
            raise SystemExit("数据库正在被占用，请先停止后端再执行") from exc

        schema = dump_schema(old)
        tables = _table_names(old)
        old_counts = {
            table: int(old.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
            for table in tables
        }

        backup_conn = sqlite3.connect(backup)
        try:
            old.backup(backup_conn)
        finally:
            backup_conn.close()
        _checkpoint(old)

        migrations_new.mkdir()
        (migrations_new / BASELINE_NAME).write_text(schema, encoding="utf-8")

        build = _open_build(new_db)
        try:
            migrate(build, migrations_new)
            build.execute("ATTACH DATABASE ? AS old", (str(db_file),))
            try:
                build.execute("BEGIN IMMEDIATE")
                counts = _copy_tables(build, tables)
                build.execute("COMMIT")
            except BaseException:
                build.execute("ROLLBACK")
                raise
            finally:
                build.execute("DETACH DATABASE old")
            _verify(build, old_counts)
            if counts != old_counts:
                raise RuntimeError(f"复制行数与旧库不一致：{counts} != {old_counts}")
        finally:
            build.close()

        if not apply:
            print(f"演练完成：新库 {new_db}，未改动真实库与迁移目录")
            return SquashReport(backup, old_counts, schema, new_db, False)

        old.close()
        if db_file.with_name(f"{db_file.name}-wal").exists():
            db_file.with_name(f"{db_file.name}-wal").unlink()
        if db_file.with_name(f"{db_file.name}-shm").exists():
            db_file.with_name(f"{db_file.name}-shm").unlink()

        os.replace(migrations_dir, migrations_old)
        try:
            os.replace(migrations_new, migrations_dir)
            os.replace(new_db, db_file)
        except BaseException:
            if migrations_dir.exists() and migrations_dir.name.endswith(f".new-{stamp}"):
                os.replace(migrations_dir, migrations_new)
            if migrations_old.exists() and not migrations_dir.exists():
                os.replace(migrations_old, migrations_dir)
            raise
        shutil.rmtree(migrations_old)
        return SquashReport(backup, old_counts, schema, db_file, True)
    finally:
        old.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATA_DIR / "pet.db")
    parser.add_argument("--migrations", type=Path, default=MIGRATIONS_DIR)
    parser.add_argument("--apply", action="store_true", help="真正替换数据库与迁移目录")
    args = parser.parse_args()

    report = squash(args.db, args.migrations, apply=args.apply)
    print(f"备份：{report.backup}")
    print(f"基线：{report.baseline.count(';')} 条语句")
    for table, count in sorted(report.tables.items()):
        print(f"  {table}: {count}")
    print("已替换" if report.applied else "未替换（演练）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
