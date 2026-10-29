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
    states: dict[str, AvatarStateOut]


class AvatarsResponse(BaseModel):
    active: str
    packs: list[AvatarPackOut]

    @classmethod
    def of(cls, catalog: AvatarCatalog) -> "AvatarsResponse":
        return cls(
            active=catalog.active,
            packs=[
                AvatarPackOut(
                    id=pack.id,
                    name=pack.name,
                    active=pack.id == catalog.active,
                    frame=AvatarFrameOut(width=pack.width, height=pack.height),
                    fallback=pack.fallback,
                    states={
                        key: AvatarStateOut(frames=list(state.frames), frame_ms=state.frame_ms)
                        for key, state in pack.states.items()
                    },
                )
                for pack in catalog.packs
            ],
        )
