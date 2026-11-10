"""每轮记忆召回：用本轮输入里的关键词到历史对话里做确定性检索。

只读、无审批、无模型参与；召回失败、没有关键词或没有命中都静默跳过，不能影响本轮回复。
检索范围排除当天（当天记录已整体重放），只取 ``master`` / ``me``（排掉子代理回执与旁注）。
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from module.memory.daily_log_service import DailyLogService

logger = logging.getLogger("pet")

RECALL_ROLES = frozenset({"master", "me"})
RECALL_MAX_QUERIES = 4
RECALL_PER_QUERY = 6
RECALL_MAX_HITS = 6
RECALL_HIT_MAX_CHARS = 160

_CJK = "\u4e00-\u9fff"
_SEGMENT = re.compile(rf"[^0-9A-Za-z{_CJK}]+")
_ASCII_WORD = re.compile(r"[0-9A-Za-z]{2,}")
_CJK_RUN = re.compile(rf"[{_CJK}]+")

# 高频虚词与寒暄几乎命中每一行，只会挤占预算。
STOPWORDS = frozenset(
    {
        "我们",
        "你们",
        "他们",
        "她们",
        "咱们",
        "自己",
        "这个",
        "那个",
        "一个",
        "一些",
        "什么",
        "怎么",
        "为什么",
        "因为",
        "所以",
        "但是",
        "然后",
        "现在",
        "今天",
        "昨天",
        "明天",
        "可以",
        "不是",
        "就是",
        "还是",
        "已经",
        "没有",
        "有点",
        "一下",
        "觉得",
        "知道",
        "时候",
        "东西",
        "事情",
        "这里",
        "那里",
        "这样",
        "那样",
        "如果",
        "虽然",
        "而且",
        "或者",
        "不过",
        "只是",
        "真的",
        "应该",
        "需要",
        "可能",
        "一样",
        "一起",
        "主人",
        "你好",
        "早安",
        "晚安",
        "在吗",
        "谢谢",
        "哈哈",
        "嗯嗯",
        "好的",
        "收到",
        "拜拜",
        "再见",
        "记得",
        "看看",
        "要不",
    }
)
# 出现在词首或词尾时，该二元组通常是虚词碎片（「的冰」「代吗」）。
FUNCTION_CHARS = frozenset(
    "的了吗呢吧啊呀哦嗯哈嘛啦呗是在有和与就都也还很太这那哪谁啥我你他她它们个把被给让"
    "从对向于为以而且但或如若则才又再更最得怎样么"
)
_TRAILING_PARTICLES = "呀啊哦嘛啦呢吧吗"


@dataclass(frozen=True)
class RecallHit:
    """一条可直接注入上下文的召回结果；日期取记录所属的自然日。"""

    id: int
    day: str
    role: str
    text: str


def keywords(text: str, *, limit: int = RECALL_MAX_QUERIES) -> tuple[str, ...]:
    """从本轮输入里取有限个可检索词：英文/数字整词，中文短词与二元组。"""
    picked: list[str] = []
    for segment in _SEGMENT.split(text):
        for word in _ASCII_WORD.findall(segment):
            # 纯数字过短（「11」）不足以定位记忆，保留年份这类四位数字与英文词。
            if word.isdigit() and len(word) < 4:
                continue
            _push(picked, word.lower())
        for run in _CJK_RUN.findall(segment):
            if len(run) <= 4:
                _push(picked, run)
            for index in range(len(run) - 1):
                first, second = run[index], run[index + 1]
                if first in FUNCTION_CHARS or second in FUNCTION_CHARS:
                    continue
                _push(picked, first + second)
        if len(picked) >= limit:
            break
    return tuple(picked[:limit])


def _push(picked: list[str], word: str) -> None:
    core = word.rstrip(_TRAILING_PARTICLES)
    if len(core) < 2 or core in STOPWORDS or core in picked:
        return
    picked.append(core)


class RecallService:
    """把本轮输入命中的历史对话整理成可直接注入的片段。"""

    def __init__(self, daily: DailyLogService) -> None:
        self._daily = daily

    def recall(self, text: str, *, today: date, persona_id: str) -> list[RecallHit]:
        terms = keywords(text)
        if not terms:
            return []
        date_to = (today - timedelta(days=1)).isoformat()
        found: dict[int, RecallHit] = {}
        matched: dict[int, int] = {}
        for term in terms:
            try:
                rows = self._daily.search(
                    term,
                    date_to=date_to,
                    newest_first=True,
                    limit=RECALL_PER_QUERY,
                    persona_id=persona_id,
                )
            except Exception:
                logger.exception("记忆召回检索失败：term=%s", term)
                continue
            for row in rows:
                if row.role not in RECALL_ROLES:
                    continue
                matched[row.id] = matched.get(row.id, 0) + 1
                if row.id in found:
                    continue
                found[row.id] = RecallHit(
                    id=row.id,
                    day=row.event_time[:10],
                    role=row.role,
                    text=_clip(row.content),
                )
        # 同一行命中越多关键词越可信，其次是越新的记录。
        ordered = sorted(found.values(), key=lambda hit: (matched[hit.id], hit.id), reverse=True)
        return ordered[:RECALL_MAX_HITS]


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= RECALL_HIT_MAX_CHARS:
        return flat
    return flat[: RECALL_HIT_MAX_CHARS - 1] + "…"
