from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from agent.capabilities.perception.interaction.contracts import InteractionConfig
from api.schemas.avatars import AvatarsResponse
from api.schemas.llm import ActiveLlmResponse, LlmProvidersResponse
from api.schemas.perception import PerceptionConfigView
from api.schemas.personas import PersonasResponse
from api.schemas.scheduler import SchedulerConfigView

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


class ModelSettingsView(BaseModel):
    """模型分组：可选 provider 清单（派生）与当前生效模型（配置）。"""

    providers: LlmProvidersResponse
    active: ActiveLlmResponse


class SettingsView(BaseModel):
    """设置页六个分组的统一读模型；`version` 供 `If-Match` 乐观锁回传。

    配置项的真源是各域领域状态，`data/settings.json` 是其持久化镜像；形象包、
    provider 可用性等派生信息在读时实时计算，不入快照（`D12`）。
    """

    version: int
    perception: PerceptionConfigView
    proactive: SchedulerConfigView
    avatar: AvatarsResponse
    persona: PersonasResponse
    model: ModelSettingsView
    interaction: InteractionConfigView
