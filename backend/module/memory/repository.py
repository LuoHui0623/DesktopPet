import json
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from infra.store.db import connect, transaction
from module.memory.contracts import DailyLogRecord, DailyLogRow, MemoryEntry, normalize_tag

logger = logging.getLogger("pet")

COLUMNS = "id, event_time, role, content, persona_id, created_at"
INSERT_SQL = (
    "INSERT INTO daily_log (event_time, role, content, persona_id, created_at) "
    "VALUES (?, ?, ?, ?, ?)"
)
DAY = "substr(event_time, 1, 10)"


def insert(conn: sqlite3.Connection, records: Sequence[DailyLogRecord]) -> list[int]:
    """逐条写入并回传自增 id，保证调用方拿到的是这一条记录的身份。"""
    ids: list[int] = []
    for record in records:
        cursor = conn.execute(
            INSERT_SQL,
            (
                record.event_time,
                record.role,
                record.content,
                record.persona_id,
                record.created_at,
            ),
        )
        ids.append(int(cursor.lastrowid))
    return ids


def by_date(
    conn: sqlite3.Connection, day: str, *, persona_id: str | None = None
) -> list[DailyLogRow]:
    if persona_id is None:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM daily_log WHERE {DAY} = ? ORDER BY created_at, id",
            (day,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM daily_log WHERE {DAY} = ? AND persona_id = ? "
            "ORDER BY created_at, id",
            (day, persona_id),
        ).fetchall()
    return [_row(row) for row in rows]


def dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(f"SELECT DISTINCT {DAY} AS day FROM daily_log ORDER BY day").fetchall()
    return [row[0] for row in rows]


def search(
    conn: sqlite3.Connection,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    newest_first: bool = False,
    limit: int | None = None,
    offset: int = 0,
    persona_id: str | None = None,
) -> list[DailyLogRow]:
    clauses: list[str] = []
    params: list[str | int] = []
    if keyword:
        clauses.append("content LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(keyword))
    if date_from:
        clauses.append(f"{DAY} >= ?")
        params.append(date_from)
    if date_to:
        clauses.append(f"{DAY} <= ?")
        params.append(date_to)
    if persona_id:
        clauses.append("persona_id = ?")
        params.append(persona_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "DESC" if newest_first else "ASC"
    paging = ""
    if limit is not None:
        paging = "LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM daily_log {where} ORDER BY created_at {order}, id {order} {paging}",
        params,
    ).fetchall()
    return [_row(row) for row in rows]


def _like_pattern(keyword: str) -> str:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _row(row: sqlite3.Row | tuple) -> DailyLogRow:
    return DailyLogRow(
        id=row[0],
        event_time=row[1],
        role=row[2],
        content=row[3],
        persona_id=row[4],
        created_at=row[5],
    )


class DbDailyLog:
    def __init__(self, db_file: Path) -> None:
        self._db_file = db_file

    def append(self, records: Sequence[DailyLogRecord]) -> list[int]:
        conn = connect(self._db_file)
        try:
            with transaction(conn):
                return insert(conn, records)
        finally:
            conn.close()

    def by_date(self, day: str, *, persona_id: str | None = None) -> list[DailyLogRow]:
        conn = connect(self._db_file)
        try:
            return by_date(conn, day, persona_id=persona_id)
        finally:
            conn.close()

    def dates(self) -> list[str]:
        conn = connect(self._db_file)
        try:
            return dates(conn)
        finally:
            conn.close()

    def search(
        self,
        keyword: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        newest_first: bool = False,
        limit: int | None = None,
        offset: int = 0,
        persona_id: str | None = None,
    ) -> list[DailyLogRow]:
        conn = connect(self._db_file)
        try:
            return search(
                conn,
                keyword,
                date_from,
                date_to,
                newest_first=newest_first,
                limit=limit,
                offset=offset,
                persona_id=persona_id,
            )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# memory_structured：条目仓储。所有函数只接收已开启事务的连接，事务边界由服务层决定。
# ---------------------------------------------------------------------------

ENTRY_COLUMNS = (
    "id, content, tags, priority, source_ref, created_at, updated_at, status_state, status_reason"
)
ENTRY_ORDER = "priority DESC, updated_at DESC, id DESC"
INSERT_ENTRY_SQL = (
    "INSERT INTO memory_structured "
    "(content, tags, priority, source_ref, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
# 手改库可能写出非法 JSON；读取与展开统一走 _tags_json 兜底，避免 json_each 直接报错。


def _tags_json(alias: str) -> str:
    return f"CASE WHEN json_valid({alias}.tags) THEN {alias}.tags ELSE '[]' END"


def entry_rows(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    tags_any: Sequence[str] = (),
    content: str | None = None,
    limit: int | None = None,
) -> list[MemoryEntry]:
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("status_state = ?")
        params.append(status)
    if content is not None:
        clauses.append("content = ?")
        params.append(content)
    wanted = sorted({normalize_tag(tag) for tag in tags_any if normalize_tag(tag)})
    if tags_any:
        if not wanted:
            return []
        marks = ", ".join("?" for _ in wanted)
        clauses.append(
            "id IN ("
            "SELECT source.id FROM memory_structured AS source, "
            f"json_each({_tags_json('source')}) AS item "
            "WHERE typeof(item.value) = 'text' "
            f"AND lower(item.value) IN ({marks}))"
        )
        params.extend(wanted)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    paging = ""
    if limit is not None:
        paging = "LIMIT ?"
        params.append(limit)
    rows = conn.execute(
        f"SELECT {ENTRY_COLUMNS} FROM memory_structured {where} ORDER BY {ENTRY_ORDER} {paging}",
        params,
    ).fetchall()
    return [_entry(row) for row in rows]


def entry_by_id(conn: sqlite3.Connection, entry_id: int) -> MemoryEntry | None:
    row = conn.execute(
        f"SELECT {ENTRY_COLUMNS} FROM memory_structured WHERE id = ?", (entry_id,)
    ).fetchone()
    return None if row is None else _entry(row)


def count_entries(conn: sqlite3.Connection, *, status: str | None = None) -> int:
    if status is None:
        row = conn.execute("SELECT COUNT(*) FROM memory_structured").fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM memory_structured WHERE status_state = ?", (status,)
        ).fetchone()
    return int(row[0])


def tag_values(conn: sqlite3.Connection) -> list[str]:
    """当前全部 tag 写法，供写入期「复用已有拼写」；包含已弃用条目。"""
    rows = conn.execute(
        "SELECT DISTINCT item.value AS value FROM memory_structured AS source, "
        f"json_each({_tags_json('source')}) AS item "
        "WHERE typeof(item.value) = 'text' ORDER BY value"
    ).fetchall()
    return [row["value"] for row in rows]


def entry_rows_with_tag(conn: sqlite3.Connection, tag: str) -> list[MemoryEntry]:
    rows = conn.execute(
        f"SELECT {ENTRY_COLUMNS} FROM memory_structured AS source "
        "WHERE id IN ("
        f"SELECT source.id FROM memory_structured AS source, "
        f"json_each({_tags_json('source')}) AS item "
        "WHERE typeof(item.value) = 'text' AND lower(item.value) = ?)"
        f" ORDER BY {ENTRY_ORDER}",
        (normalize_tag(tag),),
    ).fetchall()
    return [_entry(row) for row in rows]


def insert_entry(
    conn: sqlite3.Connection,
    *,
    content: str,
    tags: Sequence[str],
    priority: int,
    source_ref: str | None,
    created_at: str,
    updated_at: str,
) -> int:
    cursor = conn.execute(
        INSERT_ENTRY_SQL,
        (
            content,
            _dump_tags(tags),
            priority,
            source_ref,
            created_at,
            updated_at,
        ),
    )
    return int(cursor.lastrowid)


def touch_entry(conn: sqlite3.Connection, entry_id: int, updated_at: str) -> None:
    conn.execute("UPDATE memory_structured SET updated_at = ? WHERE id = ?", (updated_at, entry_id))


def update_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    updated_at: str,
    content: str | None = None,
    tags: Sequence[str] | None = None,
    priority: int | None = None,
) -> None:
    assignments = ["updated_at = ?"]
    params: list[Any] = [updated_at]
    if content is not None:
        assignments.append("content = ?")
        params.append(content)
    if tags is not None:
        assignments.append("tags = ?")
        params.append(_dump_tags(tags))
    if priority is not None:
        assignments.append("priority = ?")
        params.append(priority)
    params.append(entry_id)
    conn.execute(f"UPDATE memory_structured SET {', '.join(assignments)} WHERE id = ?", params)


def set_entry_state(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    state: str,
    reason: str | None,
    updated_at: str,
) -> None:
    conn.execute(
        "UPDATE memory_structured SET status_state = ?, status_reason = ?, updated_at = ? "
        "WHERE id = ?",
        (state, reason, updated_at, entry_id),
    )


def _dump_tags(tags: Sequence[str]) -> str:
    return json.dumps(list(tags), ensure_ascii=False)


def _entry(row: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        id=int(row["id"]),
        content=row["content"],
        tags=_parse_tags(row["tags"], entry_id=int(row["id"])),
        priority=int(row["priority"]),
        source_ref=row["source_ref"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        status_state=row["status_state"],
        status_reason=row["status_reason"],
    )


def _parse_tags(value: Any, *, entry_id: int) -> tuple[str, ...]:
    """读取容错（R1）：非法 JSON 或非字符串元素降级为空 tags 并告警，不抛异常。"""
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        logger.warning("条目 #%s 的 tags 不是合法 JSON，已降级为空：%r", entry_id, value)
        return ()
    if not isinstance(data, list):
        logger.warning("条目 #%s 的 tags 不是数组，已降级为空：%r", entry_id, value)
        return ()
    tags = tuple(item.strip() for item in data if isinstance(item, str) and item.strip())
    if len(tags) != len(data):
        logger.warning("条目 #%s 的 tags 含非字符串或空元素，已忽略：%r", entry_id, data)
    return tags
