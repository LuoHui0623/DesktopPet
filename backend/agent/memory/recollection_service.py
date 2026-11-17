"""回忆生成：把过去某一天的对话交给「记忆检索」子代理压缩成一段回忆，落库到被回忆的那一天。

派发策略是 precheck：先确定性地判定要回忆哪一天（昨天优先，没有聊天则回退到更早的最近一天），
再一次性把带前缀的任务文案派发出去；失败重试与无聊天回退共用 ``MAX_ATTEMPTS`` 次预算。
"""

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from agent.harness.approval import AutoApprovalGate
from agent.memory.contracts import DailyLogEntry
from agent.memory.daily_log_service import DailyLogService
from agent.memory.memory_service import render_daily_turns
from agent.persona.persona_service import PersonaSnapshot
from agent.spec.context import SOURCE_SYSTEM_AUTO

logger = logging.getLogger("pet")

MAX_ATTEMPTS = 2
FAILURE_PREFIX = "回忆生成失败："
NO_HISTORY_REASON = "昨天没有聊天，也没有更早的聊天记录可以回忆。"
CHAT_SIDES = frozenset({"master", "me"})
RECALL_HINT = "请用第一人称写一段一到三句话的回忆，只使用当前人设在那一天的记录。"
RECOLLECTION_TOPIC = "回忆"
# 内联素材（T-07）：当天日志带行号锚点；此前回忆链取最近 N 天、升序、超长从最旧端截断。
RECOLLECTION_CHAIN_DAYS = 30
RECOLLECTION_CHAIN_MAX_CHARS = 3000
DAILY_MATERIAL_HEADING = "【当天日志】每条前的 [#行号] 仅作定位锚点，不要写进回忆："
CHAIN_HEADING = "【此前每日回忆】只作承接前情的背景，不作为抽取来源："


def _iso(day: date | str) -> str:
    return day.isoformat() if isinstance(day, date) else date.fromisoformat(str(day)).isoformat()


def _shift(day: date | str, days: int) -> str:
    return (date.fromisoformat(_iso(day)) + timedelta(days=days)).isoformat()


def _failed(content: str) -> bool:
    return content.startswith(FAILURE_PREFIX) or content == NO_HISTORY_REASON


def has_chat(entries: Iterable[DailyLogEntry]) -> bool:
    """同一天里用户与宠物都说过话才算「有聊天」。"""
    return CHAT_SIDES <= {entry.role for entry in entries}


@dataclass(frozen=True)
class Recollection:
    day: str
    content: str
    ok: bool


@dataclass
class _InFlight:
    done: threading.Event = field(default_factory=threading.Event)
    result: Recollection | None = None


class RecollectionService:
    def __init__(self, orchestrator, daily: DailyLogService) -> None:
        self._orchestrator = orchestrator
        self._daily = daily
        self._lock = threading.Lock()
        self._inflight: dict[tuple[str, str], _InFlight] = {}

    def ensure(
        self, day: date | str, *, persona: PersonaSnapshot, turn_id: str | None = None
    ) -> Recollection:
        """确保「``day`` 的前一天」有一段回忆；同人设同一天只生成一次。"""
        target = _shift(day, -1)
        key = (persona.id, target)
        job, owner = self._claim(key)
        while not owner:
            job.done.wait()
            if job.result is not None:
                return job.result
            # 拥有者异常退出时接手，避免等待者拿不到结果。
            job, owner = self._claim(key)

        result: Recollection | None = None
        try:
            result = self._produce(target, persona, turn_id)
        except Exception:
            logger.exception("回忆生成异常：persona=%s day=%s", persona.id, target)
            result = Recollection(target, f"{FAILURE_PREFIX}生成过程异常", ok=False)
        finally:
            job.result = result
            with self._lock:
                if self._inflight.get(key) is job:
                    del self._inflight[key]
            job.done.set()
        return result

    def _claim(self, key: tuple[str, str]) -> tuple[_InFlight, bool]:
        with self._lock:
            job = self._inflight.get(key)
            if job is not None:
                return job, False
            job = _InFlight()
            self._inflight[key] = job
            return job, True

    def _produce(self, target: str, persona: PersonaSnapshot, turn_id: str | None) -> Recollection:
        existing = self._daily.read_recollection(target, persona_id=persona.id)
        if existing is not None:
            return Recollection(target, existing.content, ok=not _failed(existing.content))

        chosen = self._choose(target, persona)
        if chosen is None:
            logger.info("没有可回忆的聊天：persona=%s day=%s", persona.id, target)
            return self._store(target, NO_HISTORY_REASON, persona_id=persona.id)

        day, is_yesterday = chosen
        reason = "派发失败"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            checkpoint = self._dispatch(day, is_yesterday, turn_id, persona)
            text = (checkpoint.output or "").strip()
            if checkpoint.ok and text:
                return self._store(day, text, persona_id=persona.id)
            if checkpoint.ok:
                reason = "子代理没有返回回忆内容"
            else:
                reason = str(checkpoint.error or checkpoint.text or reason)
            logger.warning(
                "回忆生成第 %s/%s 次失败：persona=%s day=%s %s",
                attempt,
                MAX_ATTEMPTS,
                persona.id,
                day,
                reason,
            )
        return self._store(day, f"{FAILURE_PREFIX}{reason}", persona_id=persona.id)

    def _dispatch(
        self, day: str, is_yesterday: bool, turn_id: str | None, persona: PersonaSnapshot
    ):
        return self._orchestrator.dispatch(
            RECOLLECTION_TOPIC,
            _task_for(day, is_yesterday=is_yesterday, material=self._material(day, persona)),
            gate=AutoApprovalGate(),
            persona=persona,
            source=SOURCE_SYSTEM_AUTO,
            read_only=True,
            turn_id=turn_id,
            # 回忆任务是固定的“总结这一天”，不需要再拆解；失败会占用当天唯一的回忆位。
            plan_first=False,
        )

    def _material(self, day: str, persona: PersonaSnapshot) -> str:
        """被回忆日的内联素材（T-07）：带行号锚点的当天日志 + 此前每日回忆链。

        日志只取当天、不跨日；回忆链只作承接前情的背景、不作为抽取来源。
        两段都为空时返回空串，任务文案退化为纯提示。
        """
        sections: list[str] = []
        turns = render_daily_turns(
            self._daily.restore_day(day, persona_id=persona.id), with_id=True
        )
        if turns:
            sections.append(f"{DAILY_MATERIAL_HEADING}\n" + "\n".join(turns))
        chain = self._recollection_chain(day, persona.id)
        if chain:
            sections.append(f"{CHAIN_HEADING}\n" + "\n".join(chain))
        return "\n\n".join(sections)

    def _choose(self, target: str, persona: PersonaSnapshot) -> tuple[str, bool] | None:
        if self._has_chat(target, persona.id):
            return target, True
        for day in self._earlier_days(target):
            if self._has_chat(day.isoformat(), persona.id):
                return day.isoformat(), False
        return None

    def _has_chat(self, day: str, persona_id: str) -> bool:
        return has_chat(self._daily.restore_day(day, persona_id=persona_id))

    def _earlier_days(self, before: str) -> list[date]:
        limit = date.fromisoformat(before)
        days = []
        for text in self._daily.dates():
            try:
                day = date.fromisoformat(text)
            except ValueError:
                continue
            if day < limit:
                days.append(day)
        return sorted(days, reverse=True)

    def _recollection_chain(self, before: str, persona_id: str) -> list[str]:
        """此前每日回忆链：最近 ``RECOLLECTION_CHAIN_DAYS`` 天、日期升序、超长截最旧。"""
        end = date.fromisoformat(before)
        floor = end - timedelta(days=RECOLLECTION_CHAIN_DAYS)
        lines: list[str] = []
        for day in reversed(self._earlier_days(before)):
            if not floor <= day < end:
                continue
            entry = self._daily.read_recollection(day.isoformat(), persona_id=persona_id)
            if entry is None or _failed(entry.content):
                continue
            lines.append(f"{day.isoformat()}：{entry.content}")
        return _clip_oldest(lines)

    def _store(self, day: str, content: str, *, persona_id: str) -> Recollection:
        entry = self._daily.record_recollection(day, content, persona_id=persona_id)
        stored = entry.content if entry is not None else content
        return Recollection(day, stored, ok=not _failed(stored))


def _clip_oldest(lines: list[str]) -> list[str]:
    """总长超上限时从最旧端逐条丢弃（T-07「超长截最旧」）。"""
    kept = list(lines)
    while kept and sum(len(line) + 1 for line in kept) > RECOLLECTION_CHAIN_MAX_CHARS:
        kept.pop(0)
    return kept


def _task_for(day: str, *, is_yesterday: bool, material: str = "") -> str:
    prefix = "" if is_yesterday else f"昨天没有聊天，上次聊天在 {day}："
    when = f"{day}（昨天）" if is_yesterday else day
    task = f"{prefix}请回忆 {when} 的对话。{RECALL_HINT}"
    return f"{task}\n\n{material}" if material else task
