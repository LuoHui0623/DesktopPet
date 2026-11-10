"""把冻结后的候选 JSON 导入 memory_structured。

用法（在 backend 目录，默认只预览）：
    ./.venv/Scripts/python.exe -m scripts.import_structured
    ./.venv/Scripts/python.exe -m scripts.import_structured --apply

只接受 frozen_sha256 正确的候选；表为空或已包含同一批条目时写入/跳过，
出现本次候选之外的条目则整体拒绝，不做部分导入。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from constants.path import DATA_DIR
from infra.store.db import connect, transaction
from infra.store.migrate import migrate
from scripts.structured_candidates import (
    CandidateError,
    load_candidate,
    parse_source,
    validate_candidate,
)

SOURCE_FILE = "structured.md"
CANDIDATE_FILE = "structured.rewrite.json"
INSERT_SQL = (
    "INSERT INTO memory_structured "
    "(content, tags, priority, source_ref, created_at, updated_at, status_state) "
    "VALUES (?, ?, ?, ?, ?, ?, 'enabled')"
)


class ImportConflict(Exception):
    pass


@dataclass(frozen=True)
class ImportPlan:
    existing: int
    skipped: int
    missing: tuple[dict[str, Any], ...]

    @property
    def inserted(self) -> int:
        return len(self.missing)


def _tags_json(tags: list[str]) -> str:
    return json.dumps(tags, ensure_ascii=False, separators=(",", ":"))


def plan_import(conn: Any, entries: list[dict[str, Any]]) -> ImportPlan:
    rows = conn.execute(
        "SELECT source_ref, content, tags, priority, created_at FROM memory_structured"
    ).fetchall()
    existing: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["source_ref"], row["content"])
        if key in existing:
            raise ImportConflict(f"表内已存在重复条目：{key[0]} / {key[1]}")
        existing[key] = {
            "tags": row["tags"],
            "priority": row["priority"],
            "created_at": row["created_at"],
        }

    frozen = {(entry["source_ref"], entry["content"]): entry for entry in entries}
    unexpected = [key for key in existing if key not in frozen]
    if unexpected:
        sample = "、".join(f"{ref} / {content}" for ref, content in unexpected[:3])
        raise ImportConflict(
            f"表内已有 {len(unexpected)} 条不属于本次冻结候选的条目（如 {sample}）；"
            "请确认是否已人工写入，或先备份后清空表再导入"
        )

    missing: list[dict[str, Any]] = []
    for key, entry in frozen.items():
        row = existing.get(key)
        if row is None:
            missing.append(entry)
            continue
        if (
            row["tags"] != _tags_json(entry["tags"])
            or row["priority"] != entry["priority"]
            or row["created_at"] != entry["created_at"]
        ):
            raise ImportConflict(
                f"表内条目 {key[0]} / {key[1]} 与冻结候选不一致；"
                "重复导入前请先核对，避免覆盖人工修改"
            )
    return ImportPlan(len(existing), len(frozen) - len(missing), tuple(missing))


def import_entries(conn: Any, entries: list[dict[str, Any]], *, apply: bool) -> ImportPlan:
    if not apply:
        return plan_import(conn, entries)
    with transaction(conn):
        plan = plan_import(conn, entries)
        for entry in plan.missing:
            conn.execute(
                INSERT_SQL,
                (
                    entry["content"],
                    _tags_json(entry["tags"]),
                    entry["priority"],
                    entry["source_ref"],
                    entry["created_at"],
                    entry["created_at"],
                ),
            )
    return plan


def summarize(plan: ImportPlan) -> str:
    return f"已存在 {plan.existing} 条，跳过 {plan.skipped} 条，待写入 {plan.inserted} 条"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="导入冻结的 structured.md 条目候选")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只预览")
    args = parser.parse_args(argv)

    memory_dir = args.data_dir / "memory"
    source = args.source or memory_dir / SOURCE_FILE
    candidates = args.candidates or memory_dir / CANDIDATE_FILE
    db_file = args.data_dir / "pet.db"
    if not source.exists():
        print(f"源文件不存在：{source}", file=sys.stderr)
        return 1
    if not db_file.exists():
        print(f"数据库不存在：{db_file}", file=sys.stderr)
        return 1

    doc = parse_source(source.read_text(encoding="utf-8"))
    try:
        payload = load_candidate(candidates)
        entries = validate_candidate(payload, doc, require_frozen=True)
    except CandidateError as exc:
        print(f"候选校验失败：{exc}", file=sys.stderr)
        return 1

    conn = connect(db_file)
    try:
        applied = migrate(conn)
        if applied:
            print(f"数据库迁移完成：{applied}")
        plan = import_entries(conn, entries, apply=args.apply)
        total = conn.execute("SELECT COUNT(*) FROM memory_structured").fetchone()[0]
        enabled = conn.execute(
            "SELECT COUNT(*) FROM memory_structured WHERE status_state = 'enabled'"
        ).fetchone()[0]
    except ImportConflict as exc:
        print(f"导入拒绝：{exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    print(f"候选 {len(entries)} 条，覆盖 {len(doc.items)} 源行；{summarize(plan)}")
    if args.apply:
        print(f"表内现有 {total} 条，其中启用 {enabled} 条")
        print(f"冻结哈希：{payload['frozen_sha256']}")
    else:
        print("未写入；确认后加 --apply 执行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
