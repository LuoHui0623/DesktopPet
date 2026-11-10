"""把 structured.md 交给 consolidate 模型改写为条目候选，或冻结人工修正后的候选。

用法（在 backend 目录）：
    ./.venv/Scripts/python.exe -m scripts.rewrite_structured generate
    ./.venv/Scripts/python.exe -m scripts.rewrite_structured freeze

generate 只生成候选 JSON 与人工报告，不写数据库；freeze 记录 frozen_sha256。
两项均不修改 structured.md。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from config.settings import DefaultModel, ModelSettingsError, ModelSettingsSource
from constants.path import DATA_DIR
from infra.store.db import connect
from infra.store.migrate import migrate
from module.llm import LlmService
from module.llm.repository import db_usage_sink
from module.settings.settings_service import SettingsService
from scripts.structured_candidates import (
    PROMPT_VERSION,
    CandidateError,
    SourceDoc,
    build_candidate,
    freeze_candidate,
    load_candidate,
    now_iso,
    parse_source,
    render_report,
    write_candidate,
)

SOURCE_FILE = "structured.md"
CANDIDATE_FILE = "structured.rewrite.json"
REPORT_FILE = "structured.rewrite.md"
FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

SYSTEM_PROMPT = """你是桌面宠物的记忆整理助手。把结构化记忆 Markdown 的每个条目改写成可独立检索的条目候选。
规则：
1. 一条候选只表达一个可归类的事实、偏好或值；content 用简短名词或短语（建议 2–12 字），不写完整句子，不加句末标点。
2. 只有源行用「、」或「；」并列多个独立事实时才拆成多条；不要按「，」或「。」拆句。没有并列就只输出一条。
3. tags 必须原样包含源行所属分区名，再给 1–2 个类别 tag；每个 tag 2–10 字，只能含中文、英文、数字，不含标点、空格或分隔符。
4. 只能使用源行已有的事实与词，不得补充、推断或改写事实含义。
5. 日期分区由程序自动加「临时」tag，并决定 created_at/priority；你不要输出「临时」，也不要输出日期 tag。
6. 每条源行至少输出一条候选。只输出 JSON，不要解释、不要代码块：
{"entries":[{"source_ref":"structured.md:2","content":"...","tags":["分区","类别"]}]}"""


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def extract_json(content: str) -> str:
    text = content.strip()
    fenced = FENCE.search(text)
    if fenced is not None:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise CandidateError("模型输出中没有 JSON 对象")
    return text[start : end + 1]


def parse_entries(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(extract_json(raw))
    except json.JSONDecodeError as exc:
        raise CandidateError(f"模型输出不是合法 JSON：{exc}") from exc
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise CandidateError("模型输出的 entries 必须是数组")
    return entries


def build_user_prompt(doc: SourceDoc) -> str:
    lines = [
        f"源文件 {SOURCE_FILE}，共 {len(doc.items)} 条待改写：",
        "",
    ]
    for item in doc.items:
        kind = f"日期分区（{item.date}，程序会自动加「临时」）" if item.date else "稳定分区"
        lines.append(f"{item.source_ref} | 分区：{item.section} | {kind} | 原文：{item.text}")
    lines.extend(["", "请输出全部条目的 JSON。"])
    return "\n".join(lines)


def request_entries(client: Any, doc: SourceDoc) -> tuple[list[dict[str, Any]], str]:
    messages: list[Any] = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=build_user_prompt(doc)),
    ]
    response = client.invoke(messages)
    raw = text_of(response.content)
    try:
        entries = parse_entries(raw)
        build_candidate(doc, entries, model="pending", generated_at=now_iso())
    except CandidateError as exc:
        messages.extend(
            [
                AIMessage(content=raw),
                HumanMessage(
                    content=(
                        f"上一次输出有以下问题：{exc}。请只输出修正后的完整 JSON，仍遵守全部规则。"
                    )
                ),
            ]
        )
        response = client.invoke(messages)
        raw = text_of(response.content)
        entries = parse_entries(raw)
    return entries, raw


def generate(
    *,
    data_dir: Path,
    source: Path,
    candidates: Path,
    report: Path,
    model_override: str | None,
) -> int:
    text = source.read_text(encoding="utf-8")
    doc = parse_source(text)
    if not doc.items:
        print(f"源文件没有可转换的 `- ` 条目：{source}", file=sys.stderr)
        return 1

    db_file = data_dir / "pet.db"
    conn = connect(db_file)
    try:
        migrate(conn)
    finally:
        conn.close()

    llm = LlmService(
        ModelSettingsSource(),
        usage_sink=db_usage_sink(db_file),
        active_store=SettingsService(db_file),
    )
    try:
        llm.start()
    except ModelSettingsError as exc:
        print(f"LLM 配置错误：{exc}", file=sys.stderr)
        return 1
    override = None
    if model_override:
        provider, _, model = model_override.partition("/")
        if not provider or not model:
            print("--model 需要 provider/model 形式", file=sys.stderr)
            return 1
        override = DefaultModel(provider=provider, model=model)
    active = llm.active_model("consolidate")
    if active is None:
        print("未配置 consolidate 能力的模型", file=sys.stderr)
        return 1
    label = f"{active.provider}/{active.model}"

    raw = ""
    try:
        entries, raw = request_entries(llm.client_for("consolidate", override), doc)
        payload = build_candidate(
            doc,
            entries,
            model=label,
            generated_at=now_iso(),
        )
    except CandidateError as exc:
        error = f"{exc}"
        print(f"候选生成失败：{error}", file=sys.stderr)
        fallback = {
            "version": 1,
            "prompt_version": PROMPT_VERSION,
            "model": label,
            "generated_at": now_iso(),
            "source": {
                "name": SOURCE_FILE,
                "sha256": doc.sha256,
                "line_count": doc.line_count,
                "item_count": len(doc.items),
                "earliest_date": doc.earliest_date,
            },
            "entries": [],
            "frozen_sha256": None,
        }
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            render_report(fallback, doc, errors=[error], raw_output=raw),
            encoding="utf-8",
        )
        print(f"已把失败原因与模型原始输出写入报告：{report}", file=sys.stderr)
        return 2

    write_candidate(candidates, payload)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(payload, doc), encoding="utf-8")
    frozen = "已冻结" if payload["frozen_sha256"] else "未冻结（需人工核对后 freeze）"
    print(
        f"已生成 {len(payload['entries'])} 条候选，覆盖 {len(doc.items)}/{len(doc.items)} 源行；"
        f"模型 {label}；{frozen}"
    )
    print(f"候选：{candidates}")
    print(f"报告：{report}")
    return 0


def freeze(*, source: Path, candidates: Path, report: Path) -> int:
    doc = parse_source(source.read_text(encoding="utf-8"))
    try:
        payload = freeze_candidate(load_candidate(candidates), doc)
    except CandidateError as exc:
        print(f"冻结失败：{exc}", file=sys.stderr)
        return 1
    write_candidate(candidates, payload)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(payload, doc), encoding="utf-8")
    print(
        f"已冻结 {len(payload['entries'])} 条候选，覆盖 {len(doc.items)} 源行；"
        f"sha256 {payload['frozen_sha256']}"
    )
    print(f"候选：{candidates}")
    print(f"报告：{report}")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="structured.md 条目候选生成与冻结")
    parser.add_argument("command", choices=("generate", "freeze"))
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--model", help="generate 时覆盖模型，格式 provider/model")
    args = parser.parse_args(argv)

    memory_dir = args.data_dir / "memory"
    source = args.source or memory_dir / SOURCE_FILE
    candidates = args.candidates or memory_dir / CANDIDATE_FILE
    report = args.report or memory_dir / REPORT_FILE
    if not source.exists():
        print(f"源文件不存在：{source}", file=sys.stderr)
        return 1
    if args.command == "generate":
        return generate(
            data_dir=args.data_dir,
            source=source,
            candidates=candidates,
            report=report,
            model_override=args.model,
        )
    return freeze(source=source, candidates=candidates, report=report)


if __name__ == "__main__":
    raise SystemExit(main())
