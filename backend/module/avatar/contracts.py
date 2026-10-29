from dataclasses import dataclass


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


@dataclass(frozen=True)
class AvatarCatalog:
    active: str
    packs: tuple[AvatarPack, ...]
