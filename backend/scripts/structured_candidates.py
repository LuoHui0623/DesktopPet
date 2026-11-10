"""structured.md → 条目候选的解析、校验与冻结（T-01 一次性转换）。

候选 JSON 是唯一导入输入：人工过目修正后执行 freeze 写入 frozen_sha256，
导入命令只接受冻结结果，源文件或候选被改动都会被拒绝。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from module.memory.contracts import MemoryEntryCreate

SOURCE_NAME = "structured.md"
PROMPT_VERSION = "structured-rewrite-v1"
CANDIDATE_VERSION = 1
BASELINE_DATE = "2026-11-01"
CONVERSION_OFFSET = "+08:00"
DATE_HEADING = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s+(.*))?$")


class CandidateError(Exception):
    pass


@dataclass(frozen=True)
class SourceItem:
    line: int
    section: str
    text: str
    date: str | None

    @property
    def source_ref(self) -> str:
        return f"{SOURCE_NAME}:{self.line}"

    @property
    def source_sha256(self) -> str:
        return sha256_text(self.text)

    @property
    def priority(self) -> int:
        return 1 if self.date else 2

    @property
    def created_at(self) -> str:
        return f"{self.date or BASELINE_DATE}T00:00:00{CONVERSION_OFFSET}"


@dataclass(frozen=True)
class SourceDoc:
    text: str
    sha256: str
    line_count: int
    items: tuple[SourceItem, ...]
    earliest_date: str

    def by_line(self, line: int) -> SourceItem:
        for item in self.items:
            if item.line == line:
                return item
        raise CandidateError(f"源文件第 {line} 行不是可转换的 `- ` 条目")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_source(text: str) -> SourceDoc:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    items: list[SourceItem] = []
    section: str | None = None
    section_date: str | None = None
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if line.startswith("## "):
            heading = line[3:].strip()
            match = DATE_HEADING.match(heading)
            if match is not None:
                section_date = match.group(1)
                # 日期字符串不进入 tag；标题剩下的部分才是分区名。
                section = (match.group(2) or "今天的点滴").strip()
            else:
                section_date = None
                section = heading
            continue
        if not line.startswith("- "):
            continue
        body = line[2:].strip()
        if not body:
            continue
        if section is None:
            raise CandidateError(f"第 {number} 行没有所属分区，无法确定 tags")
        items.append(SourceItem(number, section, body, section_date))
    dates = [item.date for item in items if item.date]
    earliest = min(dates) if dates else BASELINE_DATE
    return SourceDoc(text, sha256_text(text), len(lines), tuple(items), earliest)


def source_summary(doc: SourceDoc) -> dict[str, Any]:
    return {
        "name": SOURCE_NAME,
        "sha256": doc.sha256,
        "line_count": doc.line_count,
        "item_count": len(doc.items),
        "earliest_date": doc.earliest_date,
    }


def with_section_tags(item: SourceItem, tags: list[str]) -> list[str]:
    """分区 tag 必须存在；日期分区自动补 `临时`，其余 tag 由候选提供。"""
    merged = list(tags)
    if item.section not in merged:
        merged.insert(0, item.section)
    if item.date is not None and "临时" not in merged:
        merged.append("临时")
    return merged


def normalize_entry(item: SourceItem, content: str, tags: list[str]) -> dict[str, Any]:
    try:
        entry = MemoryEntryCreate(
            content=content,
            tags=with_section_tags(item, tags),
            priority=item.priority,
            source_ref=item.source_ref,
        )
    except ValidationError as exc:
        detail = "；".join(error["msg"] for error in exc.errors())
        raise CandidateError(f"{item.source_ref} 候选不合法：{detail}") from exc
    return {
        "source_ref": item.source_ref,
        "source_line": item.line,
        "source_text": item.text,
        "source_sha256": item.source_sha256,
        "content": entry.content,
        "tags": entry.tags,
        "priority": entry.priority,
        "created_at": item.created_at,
    }


def build_candidate(
    doc: SourceDoc,
    raw_entries: list[dict[str, Any]],
    *,
    model: str,
    generated_at: str,
) -> dict[str, Any]:
    entries = sorted(
        (normalize_raw_entry(doc, raw) for raw in raw_entries),
        key=lambda entry: (entry["source_line"], entry["content"]),
    )
    payload = {
        "version": CANDIDATE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "generated_at": generated_at,
        "source": source_summary(doc),
        "entries": entries,
        "frozen_sha256": None,
    }
    validate_candidate(payload, doc)
    return payload


def normalize_raw_entry(doc: SourceDoc, raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CandidateError("候选条目必须是对象")
    line = parse_source_ref(raw.get("source_ref"))
    item = doc.by_line(line)
    content = raw.get("content")
    tags = raw.get("tags")
    if not isinstance(content, str):
        raise CandidateError(f"{item.source_ref} 缺少 content")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise CandidateError(f"{item.source_ref} 的 tags 必须是字符串数组")
    return normalize_entry(item, content, tags)


def parse_source_ref(value: Any) -> int:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise CandidateError(f"source_ref 不合法：{value!r}")
    text = value.strip()
    if text.startswith(f"{SOURCE_NAME}:"):
        text = text.split(":", 1)[1]
    try:
        return int(text)
    except ValueError as exc:
        raise CandidateError(f"source_ref 不合法：{value!r}") from exc


def validate_candidate(
    payload: dict[str, Any],
    doc: SourceDoc,
    *,
    require_frozen: bool = False,
) -> list[dict[str, Any]]:
    if payload.get("version") != CANDIDATE_VERSION:
        raise CandidateError(f"候选版本不支持：{payload.get('version')!r}")
    summary = payload.get("source")
    if not isinstance(summary, dict):
        raise CandidateError("候选缺少 source 信息")
    expected = source_summary(doc)
    for key in ("name", "sha256", "line_count", "item_count", "earliest_date"):
        if summary.get(key) != expected[key]:
            raise CandidateError(f"源文件已变化：source.{key} 与当前 {SOURCE_NAME} 不一致")

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CandidateError("候选 entries 不能为空")

    entries: list[dict[str, Any]] = []
    covered: set[int] = set()
    for index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict):
            raise CandidateError(f"第 {index} 条候选不是对象")
        line = parse_source_ref(raw.get("source_ref"))
        item = doc.by_line(line)
        if raw.get("source_sha256") != item.source_sha256:
            raise CandidateError(f"{item.source_ref} 的 source_sha256 与当前源行不一致")
        if raw.get("source_text") != item.text:
            raise CandidateError(f"{item.source_ref} 的 source_text 与当前源行不一致")
        if raw.get("source_line") != item.line:
            raise CandidateError(f"{item.source_ref} 的 source_line 与 source_ref 不一致")
        content = raw.get("content")
        tags = raw.get("tags")
        if not isinstance(content, str):
            raise CandidateError(f"{item.source_ref} 缺少 content")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise CandidateError(f"{item.source_ref} 的 tags 必须是字符串数组")
        normalized = normalize_entry(item, content, tags)
        if raw.get("priority") != normalized["priority"]:
            raise CandidateError(f"{item.source_ref} 的 priority 应由分区决定")
        if raw.get("created_at") != normalized["created_at"]:
            raise CandidateError(f"{item.source_ref} 的 created_at 应由分区决定")
        entries.append(normalized)
        covered.add(item.line)

    missing = [item for item in doc.items if item.line not in covered]
    if missing:
        refs = "、".join(item.source_ref for item in missing)
        raise CandidateError(f"以下源行没有候选（{len(missing)} 条）：{refs}")

    if require_frozen:
        if payload.get("frozen_sha256") != compute_frozen_sha256(payload):
            raise CandidateError("冻结哈希不匹配：候选被修改后尚未重新 freeze")

    return sorted(entries, key=lambda entry: (entry["source_line"], entry["content"]))


def compute_frozen_sha256(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "frozen_sha256"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(canonical)


def freeze_candidate(payload: dict[str, Any], doc: SourceDoc) -> dict[str, Any]:
    entries = validate_candidate(payload, doc)
    frozen = {
        **{key: value for key, value in payload.items() if key != "entries"},
        "entries": entries,
    }
    frozen["frozen_sha256"] = compute_frozen_sha256(frozen)
    validate_candidate(frozen, doc, require_frozen=True)
    return frozen


def load_candidate(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CandidateError(f"候选文件不存在：{path}") from exc
    except (OSError, ValueError) as exc:
        raise CandidateError(f"候选文件无法读取：{exc}") from exc
    if not isinstance(data, dict):
        raise CandidateError("候选文件顶层必须是对象")
    return data


def write_candidate(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def render_report(
    payload: dict[str, Any],
    doc: SourceDoc,
    *,
    errors: list[str] | None = None,
    raw_output: str | None = None,
) -> str:
    source = payload["source"]
    entries = payload.get("entries") or []
    covered = {entry["source_line"] for entry in entries}
    lines = [
        "# structured.md 转换候选核对报告",
        "",
        f"- 源文件：`{source['name']}`，sha256 `{source['sha256']}`",
        f"- 源行数：{source['line_count']}，待转换条目：{source['item_count']}，"
        f"已覆盖：{len(covered)}/{source['item_count']}",
        f"- 模型：`{payload.get('model')}`，提示词版本：`{payload.get('prompt_version')}`",
        f"- 生成时间：{payload.get('generated_at')}",
        f"- 冻结哈希：`{payload.get('frozen_sha256') or '未冻结'}`",
        "",
    ]
    if errors:
        lines.extend(["## 待修正", "", *[f"- {error}" for error in errors], ""])
    lines.extend(
        [
            "## 候选条目",
            "",
            "| 源行 | 分区 | 原文 | content | tags | priority | created_at |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for entry in entries:
        item = doc.by_line(entry["source_line"])
        lines.append(
            "| {line} | {section} | {text} | {content} | {tags} | {priority} | {created} |".format(
                line=entry["source_line"],
                section=_cell(item.section),
                text=_cell(item.text),
                content=_cell(entry["content"]),
                tags=_cell(" / ".join(entry["tags"])),
                priority=entry["priority"],
                created=entry["created_at"],
            )
        )
    missing = [item for item in doc.items if item.line not in covered]
    if missing:
        lines.extend(["", "## 未覆盖源行", ""])
        lines.extend(f"- `{item.source_ref}`：{item.text}" for item in missing)
    if raw_output is not None:
        lines.extend(["", "## 模型原始输出", "", "```text", raw_output.strip(), "```"])
    return "\n".join(lines) + "\n"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
