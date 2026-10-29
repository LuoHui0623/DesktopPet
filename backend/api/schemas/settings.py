from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from api.schemas.avatars import AvatarsResponse
from api.schemas.perception import PerceptionConfigView
from api.schemas.scheduler import SchedulerConfigView
from module.interaction.contracts import InteractionConfig

InteractionKind = Literal["click", "hover", "drag", "menu"]


class InteractionConfigView(BaseModel):
    report_enabled_kinds: list[InteractionKind]
    hover_dwell_ms: int
    report_interval_ms: int
    reaction_ms: int

    @classmethod
    def of(cls, config: InteractionConfig) -> "InteractionConfigView":
        return cls(
            report_enabled_kinds=list(config.report_enabled_kinds),
            hover_dwell_ms=config.hover_dwell_ms,
            report_interval_ms=config.report_interval_ms,
            reaction_ms=config.reaction_ms,
        )


class InteractionConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_enabled_kinds: list[InteractionKind]
    hover_dwell_ms: StrictInt = Field(ge=100, le=3000)
    report_interval_ms: StrictInt = Field(ge=0, le=10_000)
    reaction_ms: StrictInt = Field(ge=0, le=3000)


class SettingsView(BaseModel):
    """设置页四个分组的统一读模型；真源仍是 app_setting 与形象清单。"""

    perception: PerceptionConfigView
    proactive: SchedulerConfigView
    avatar: AvatarsResponse
    interaction: InteractionConfigView
