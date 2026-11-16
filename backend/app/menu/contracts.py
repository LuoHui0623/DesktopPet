from dataclasses import dataclass
from typing import Protocol

from agent.capabilities.perception.gate.scheduler import SchedulerConfig
from app.avatar.contracts import AvatarCatalog


@dataclass(frozen=True)
class MenuItem:
    id: str
    label: str
    enabled: bool
    checked: bool | None
    children: tuple["MenuItem", ...] = ()


@dataclass(frozen=True)
class Menu:
    items: tuple[MenuItem, ...]


class AvatarCatalogReader(Protocol):
    def catalog(self) -> AvatarCatalog: ...


class SchedulerConfigReader(Protocol):
    def config(self) -> SchedulerConfig: ...
