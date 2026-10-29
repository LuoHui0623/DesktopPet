from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

InteractionKind = Literal["click", "hover", "drag", "menu"]

INTERACTION_KINDS: tuple[InteractionKind, ...] = ("click", "hover", "drag", "menu")

INTERACTION_LABELS: dict[InteractionKind, str] = {
    "click": "单击宠物",
    "hover": "悬停查看宠物",
    "drag": "拖动宠物",
    "menu": "右键打开菜单",
}


@dataclass(frozen=True)
class InteractionConfig:
    report_enabled_kinds: tuple[InteractionKind, ...]
    hover_dwell_ms: int
    report_interval_ms: int
    reaction_ms: int


@dataclass(frozen=True)
class Interaction:
    kind: InteractionKind
    at: datetime

    @property
    def label(self) -> str:
        return INTERACTION_LABELS[self.kind]


@dataclass(frozen=True)
class InteractionOutcome:
    accepted: bool
    reason: str | None = None


class InteractionReader(Protocol):
    def drain(self) -> tuple[Interaction, ...]: ...
