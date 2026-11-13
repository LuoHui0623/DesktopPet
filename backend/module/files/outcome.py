"""结算结果：逐文件给出可判读的结局，回执文案与留意标记都由结局派生。"""

from dataclasses import dataclass

STATUS_COMMITTED = "committed"
STATUS_REJECTED = "rejected"
STATUS_CONFLICT = "conflict"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# 已了结的结局不需要模型再处理；其余结局（拒绝、冲突、失败）都要把回执交回模型。
_SETTLED = frozenset({STATUS_COMMITTED, STATUS_SKIPPED})


@dataclass(frozen=True)
class SettleResult:
    """单个文件的结算结局；reason 只放失败原因，receipt 是给模型的原文。"""

    status: str
    path: str = ""
    hunks: int = 0
    reason: str | None = None
    receipt: str = ""

    @property
    def needs_model(self) -> bool:
        return self.status not in _SETTLED


@dataclass(frozen=True)
class SettlementOutcome:
    """一次结算的全部逐文件结局；空结果代表没有需要结算的草稿。"""

    results: tuple[SettleResult, ...] = ()

    @property
    def receipts(self) -> tuple[str, ...]:
        return tuple(result.receipt for result in self.results if result.receipt)

    @property
    def needs_model(self) -> bool:
        return any(result.needs_model for result in self.results)

    @property
    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for result in self.results:
            tally[result.status] = tally.get(result.status, 0) + 1
        return tally
