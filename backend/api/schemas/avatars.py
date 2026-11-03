from pydantic import BaseModel, ConfigDict

from module.avatar.contracts import AvatarCatalog


class ActivateAvatarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str


class AvatarFrameOut(BaseModel):
    width: int
    height: int


class AvatarStateOut(BaseModel):
    frames: list[str]
    frame_ms: int


class AvatarPackOut(BaseModel):
    id: str
    name: str
    active: bool
    frame: AvatarFrameOut
    fallback: str
    static: str
    states: dict[str, AvatarStateOut]


class AvatarsResponse(BaseModel):
    persona: str
    active: str | None
    preset_states: list[str]
    packs: list[AvatarPackOut]

    @classmethod
    def of(cls, catalog: AvatarCatalog) -> "AvatarsResponse":
        return cls(
            persona=catalog.persona,
            active=catalog.active,
            preset_states=list(catalog.preset_states),
            packs=[
                AvatarPackOut(
                    id=pack.id,
                    name=pack.name,
                    active=pack.id == catalog.active,
                    frame=AvatarFrameOut(width=pack.width, height=pack.height),
                    fallback=pack.fallback,
                    static=pack.static,
                    states={
                        key: AvatarStateOut(frames=list(state.frames), frame_ms=state.frame_ms)
                        for key, state in pack.states.items()
                    },
                )
                for pack in catalog.packs
            ],
        )
