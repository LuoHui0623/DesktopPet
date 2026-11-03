from dataclasses import dataclass

from module.avatar.constants import PRESET_STATES


@dataclass(frozen=True)
class AvatarState:
    frames: tuple[str, ...]
    frame_ms: int


@dataclass(frozen=True)
class AvatarPack:
    id: str
    name: str
    width: int
    height: int
    fallback: str
    states: dict[str, AvatarState]
    static: str = ""


@dataclass(frozen=True)
class AvatarCatalog:
    persona: str
    active: str | None
    packs: tuple[AvatarPack, ...]
    preset_states: tuple[str, ...] = PRESET_STATES
