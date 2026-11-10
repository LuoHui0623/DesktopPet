import sqlite3
from pathlib import Path

import pytest

from infra.store.db import connect
from infra.store.migrate import migrate
from scripts.import_structured import ImportConflict, import_entries, plan_import
from scripts.rewrite_structured import extract_json, parse_entries
from scripts.structured_candidates import (
    CandidateError,
    build_candidate,
    freeze_candidate,
    parse_source,
    validate_candidate,
)

SOURCE = """## 稳定
- 主人喜欢脆柿子、葡萄。
- 主人的香水：YSL。
## 2026-11-01 今天的点滴
- 主人核对时间。
"""

RAW_ENTRIES = [
    {"source_ref": "structured.md:2", "content": "脆柿子", "tags": ["水果"]},
    {"source_ref": "structured.md:2", "content": "葡萄", "tags": ["水果"]},
    {"source_ref": "structured.md:3", "content": "YSL", "tags": ["香水"]},
    {"source_ref": "structured.md:5", "content": "核对时间", "tags": []},
]


def make_payload(doc=None, raw=None):
    doc = doc or parse_source(SOURCE)
    return build_candidate(
        doc,
        raw or RAW_ENTRIES,
        model="test/model",
        generated_at="2026-11-10T12:00:00+08:00",
    )


def make_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "pet.db")
    migrate(conn)
    return conn


def test_parse_source_collects_items_sections_and_dates():
    doc = parse_source(SOURCE)
    assert [item.line for item in doc.items] == [2, 3, 5]
    assert [item.section for item in doc.items] == ["稳定", "稳定", "今天的点滴"]
    assert doc.items[2].date == "2026-11-01"
    assert doc.items[2].priority == 1
    assert doc.items[0].priority == 2
    assert doc.items[0].created_at == "2026-11-01T00:00:00+08:00"
    assert doc.items[2].created_at == "2026-11-01T00:00:00+08:00"
    assert doc.earliest_date == "2026-11-01"


def test_parse_source_uses_baseline_when_no_date_section():
    doc = parse_source("## 稳定\n- 值。\n")
    assert doc.items[0].created_at == "2026-11-01T00:00:00+08:00"
    assert doc.earliest_date == "2026-11-01"


def test_build_candidate_adds_section_and_temporary_tags():
    doc = parse_source(SOURCE)
    payload = make_payload(doc)
    assert payload["frozen_sha256"] is None
    entries = payload["entries"]
    assert len(entries) == 4
    assert [entry["source_line"] for entry in entries] == [2, 2, 3, 5]
    assert entries[0]["tags"] == ["稳定", "水果"]
    assert entries[-1]["tags"] == ["今天的点滴", "临时"]
    assert all("2026-11-01" not in tags for entry in entries for tags in entry["tags"])
    assert entries[-1]["priority"] == 1
    assert entries[0]["priority"] == 2
    validate_candidate(payload, doc)


def test_build_candidate_rejects_missing_source_line():
    doc = parse_source(SOURCE)
    with pytest.raises(CandidateError, match="没有候选"):
        make_payload(doc, RAW_ENTRIES[:3])


def test_build_candidate_rejects_invalid_tag():
    doc = parse_source(SOURCE)
    raw = [*RAW_ENTRIES[:3], {"source_ref": "structured.md:5", "content": "值", "tags": ["水果!"]}]
    with pytest.raises(CandidateError, match="只能包含中文、英文与数字"):
        make_payload(doc, raw)


def test_freeze_is_stable_and_detects_tampering():
    doc = parse_source(SOURCE)
    frozen = freeze_candidate(make_payload(doc), doc)
    assert len(frozen["frozen_sha256"]) == 64
    assert freeze_candidate(frozen, doc)["frozen_sha256"] == frozen["frozen_sha256"]
    validate_candidate(frozen, doc, require_frozen=True)

    tampered = {**frozen, "entries": [dict(entry) for entry in frozen["entries"]]}
    tampered["entries"][0]["content"] = "葡萄干"
    with pytest.raises(CandidateError, match="冻结哈希不匹配"):
        validate_candidate(tampered, doc, require_frozen=True)


def test_validate_rejects_source_drift():
    frozen = freeze_candidate(make_payload(), parse_source(SOURCE))
    changed = parse_source(SOURCE.replace("脆柿子", "柿子"))
    with pytest.raises(CandidateError, match="源文件已变化"):
        validate_candidate(frozen, changed, require_frozen=True)


def test_validate_requires_frozen_hash():
    payload = make_payload()
    with pytest.raises(CandidateError, match="冻结哈希不匹配"):
        validate_candidate(payload, parse_source(SOURCE), require_frozen=True)


def test_import_is_idempotent_and_skips_existing_rows(tmp_path: Path):
    doc = parse_source(SOURCE)
    payload = freeze_candidate(make_payload(doc), doc)
    entries = validate_candidate(payload, doc, require_frozen=True)
    conn = make_conn(tmp_path)
    try:
        plan = import_entries(conn, entries, apply=True)
        assert plan.inserted == 4
        assert conn.execute("SELECT COUNT(*) FROM memory_structured").fetchone()[0] == 4
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM memory_structured WHERE status_state = 'enabled'"
            ).fetchone()[0]
            == 4
        )

        again = import_entries(conn, entries, apply=True)
        assert (again.inserted, again.skipped) == (0, 4)
        assert conn.execute("SELECT COUNT(*) FROM memory_structured").fetchone()[0] == 4
    finally:
        conn.close()


def test_import_dry_run_does_not_write(tmp_path: Path):
    doc = parse_source(SOURCE)
    entries = validate_candidate(make_payload(doc), doc)
    conn = make_conn(tmp_path)
    try:
        plan = import_entries(conn, entries, apply=False)
        assert plan.inserted == 4
        assert conn.execute("SELECT COUNT(*) FROM memory_structured").fetchone()[0] == 0
    finally:
        conn.close()


def test_import_rejects_unrelated_existing_rows(tmp_path: Path):
    doc = parse_source(SOURCE)
    entries = validate_candidate(make_payload(doc), doc)
    conn = make_conn(tmp_path)
    try:
        conn.execute(
            "INSERT INTO memory_structured (content, tags, source_ref, created_at, updated_at) "
            "VALUES ('人工条目', '[\"其他\"]', 'other.md:1', 't', 't')"
        )
        with pytest.raises(ImportConflict, match="不属于本次冻结候选"):
            plan_import(conn, entries)
    finally:
        conn.close()


def test_import_rejects_modified_existing_row(tmp_path: Path):
    doc = parse_source(SOURCE)
    entries = validate_candidate(make_payload(doc), doc)
    conn = make_conn(tmp_path)
    try:
        import_entries(conn, entries, apply=True)
        conn.execute(
            "UPDATE memory_structured SET tags = '[\"别的\"]' WHERE source_ref = 'structured.md:2'"
        )
        with pytest.raises(ImportConflict, match="与冻结候选不一致"):
            plan_import(conn, entries)
    finally:
        conn.close()


def test_extract_json_handles_fences_and_parse_entries_accepts_object():
    body = '{"entries":[{"source_ref":"structured.md:2","content":"值","tags":["分区"]}]}'
    fenced = f"说明\n```json\n{body}\n```"
    assert parse_entries(fenced) == [
        {"source_ref": "structured.md:2", "content": "值", "tags": ["分区"]}
    ]
    with pytest.raises(CandidateError, match="没有 JSON 对象"):
        extract_json("没有输出")
