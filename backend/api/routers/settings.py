"""设置页的唯一配置写入通道（`D12`）。

六个分组各一个 PUT：`perception`、`proactive`、`avatar`、`persona`、`model`、
`interaction`。写入顺序由 `SettingsCoordinator` 固化——校验 `If-Match` 版本、
调域方法改领域状态、投射快照、落盘；本层只做传输契约组装与错误映射。
"""

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from agent.capabilities.perception.collector.service import PerceptionService
from agent.capabilities.perception.events.models import InvalidPerceptionConfig
from agent.capabilities.perception.gate.scheduler import SECTION as SCHEDULER_SECTION
from agent.capabilities.perception.gate.scheduler import (
    InvalidSchedulerConfig,
    SchedulerConfig,
    SchedulerRuntime,
)
from agent.capabilities.perception.gate.settings import SECTION as PERCEPTION_SECTION
from agent.capabilities.perception.interaction.config import SECTION as INTERACTION_SECTION
from agent.capabilities.perception.interaction.interaction_service import InteractionService
from agent.llm.active import SECTION as LLM_SECTION
from agent.llm.llm_service import LlmService, ModelSelectionError
from agent.persona.persona_service import SECTION as PERSONA_SECTION
from agent.persona.persona_service import PersonaService, PersonaUnavailableError
from api.errors import error_response
from api.routers.avatars import catalog_view
from api.routers.llm import active_view, providers_view
from api.routers.perception import config_view as perception_config_view
from api.routers.personas import persona_view
from api.routers.scheduler import config_view as scheduler_config_view
from api.schemas.avatars import ActivateAvatarIn
from api.schemas.llm import ActiveLlmRequest
from api.schemas.perception import PerceptionConfigIn
from api.schemas.personas import PersonaActivateRequest
from api.schemas.scheduler import SchedulerConfigIn
from api.schemas.settings import (
    InteractionConfigIn,
    InteractionConfigView,
    ModelSettingsView,
    SettingsView,
)
from app.avatar.avatar_service import SECTION as AVATAR_SECTION
from app.avatar.avatar_service import AvatarNotFoundError, AvatarService
from app.settings.coordinator import SettingsCoordinator, SettingsVersionConflict
from config.settings import CAPABILITY_VARS, DefaultModel

router = APIRouter(prefix="/api/settings", tags=["settings"])

CONFLICT = {409: {"description": "If-Match 版本与当前快照不一致"}}
VERSION_REQUIRED = "缺少 If-Match 版本号，设置写入需要携带当前 version"


def _perception(request: Request) -> PerceptionService:
    service: PerceptionService | None = request.app.state.perception
    if service is None:
        raise HTTPException(status_code=503, detail="感知服务未配置")
    return service


def _scheduler(request: Request) -> SchedulerRuntime:
    runtime: SchedulerRuntime | None = getattr(request.app.state, "scheduler", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="调度服务未配置")
    return runtime


def _interaction(request: Request) -> InteractionService:
    service: InteractionService | None = request.app.state.interaction
    if service is None:
        raise HTTPException(status_code=503, detail="互动服务未配置")
    return service


def _avatars(request: Request) -> AvatarService:
    service: AvatarService | None = request.app.state.avatars
    if service is None:
        raise HTTPException(status_code=503, detail="形象服务未配置")
    return service


def _personas(request: Request) -> PersonaService:
    service: PersonaService | None = request.app.state.personas
    if service is None:
        raise HTTPException(status_code=503, detail="人设服务未配置")
    return service


def _llm(request: Request) -> LlmService:
    service: LlmService | None = request.app.state.llm_service
    if service is None:
        raise HTTPException(status_code=503, detail="LLM 服务未配置")
    return service


def _coordinator(request: Request) -> SettingsCoordinator:
    coordinator: SettingsCoordinator | None = getattr(request.app.state, "settings", None)
    if coordinator is None:
        raise HTTPException(status_code=503, detail="设置文档未配置")
    return coordinator


def _version(if_match: str | None) -> int:
    """解析 `If-Match`；缺失返回 428，非整数返回 400。"""
    if if_match is None:
        raise HTTPException(status_code=428, detail=VERSION_REQUIRED)
    try:
        return int(if_match.strip().strip('"'))
    except ValueError:
        raise HTTPException(status_code=400, detail="If-Match 必须是整数版本号") from None


def interaction_view(service: InteractionService) -> InteractionConfigView:
    return InteractionConfigView.of(service.config())


def build_view(request: Request) -> SettingsView:
    perception = _perception(request)
    catalog = _avatars(request).catalog()
    coordinator: SettingsCoordinator | None = getattr(request.app.state, "settings", None)
    if coordinator is not None:
        # 记住的形象包缺失时会回退组默认包，这条自愈也要落进快照。
        coordinator.project(AVATAR_SECTION)
    llm = _llm(request)
    return SettingsView(
        version=coordinator.version if coordinator is not None else 0,
        perception=perception_config_view(perception.config()),
        proactive=scheduler_config_view(perception.scheduler.config()),
        avatar=catalog_view(catalog),
        persona=persona_view(_personas(request)),
        model=ModelSettingsView(providers=providers_view(llm), active=active_view(llm)),
        interaction=interaction_view(_interaction(request)),
    )


@router.get("", operation_id="get_settings", response_model=SettingsView)
def get_settings(request: Request) -> SettingsView:
    return build_view(request)


@router.put(
    "/perception",
    operation_id="update_settings_perception",
    response_model=SettingsView,
    responses={**CONFLICT, 422: {"description": "感知配置取值不合法"}},
)
def update_perception(
    request: Request,
    body: PerceptionConfigIn,
    if_match: str | None = Header(default=None),
) -> Response | SettingsView:
    version = _version(if_match)
    try:
        _coordinator(request).save(
            PERCEPTION_SECTION,
            lambda: _perception(request).write_config(
                enabled=body.enabled,
                foreground_enabled=body.foreground_enabled,
                idle_away_minutes=body.idle_away_minutes,
                sit_minutes=body.sit_minutes,
            ),
            version=version,
        )
    except SettingsVersionConflict as exc:
        return error_response(409, "settings_version_conflict", str(exc))
    except InvalidPerceptionConfig as exc:
        return error_response(422, "invalid_perception_config", str(exc))
    return build_view(request)


@router.put(
    "/proactive",
    operation_id="update_settings_proactive",
    response_model=SettingsView,
    responses={**CONFLICT, 422: {"description": "调度配置取值不合法"}},
)
def update_proactive(
    request: Request,
    body: SchedulerConfigIn,
    if_match: str | None = Header(default=None),
) -> Response | SettingsView:
    version = _version(if_match)
    config = SchedulerConfig(
        enabled=body.enabled,
        min_interval_minutes=body.min_interval_minutes,
        max_per_day=body.max_per_day,
        dnd_start=body.dnd_start,
        dnd_end=body.dnd_end,
    )
    try:
        _coordinator(request).save(
            SCHEDULER_SECTION,
            lambda: _scheduler(request).write_config(config),
            version=version,
        )
    except SettingsVersionConflict as exc:
        return error_response(409, "settings_version_conflict", str(exc))
    except InvalidSchedulerConfig as exc:
        return error_response(422, "invalid_scheduler_config", str(exc))
    return build_view(request)


@router.put(
    "/avatar",
    operation_id="update_settings_avatar",
    response_model=SettingsView,
    responses={**CONFLICT, 404: {"description": "形象包不存在"}},
)
def update_avatar(
    request: Request,
    body: ActivateAvatarIn,
    if_match: str | None = Header(default=None),
) -> Response | SettingsView:
    version = _version(if_match)
    try:
        _coordinator(request).save(
            AVATAR_SECTION,
            lambda: _avatars(request).activate(body.id),
            version=version,
        )
    except SettingsVersionConflict as exc:
        return error_response(409, "settings_version_conflict", str(exc))
    except AvatarNotFoundError:
        return error_response(404, "avatar_not_found", f"形象包不存在：{body.id}")
    return build_view(request)


@router.put(
    "/persona",
    operation_id="update_settings_persona",
    response_model=SettingsView,
    responses={**CONFLICT, 422: {"description": "人设不存在，或解析失败不可用"}},
)
def update_persona(
    request: Request,
    body: PersonaActivateRequest,
    if_match: str | None = Header(default=None),
) -> Response | SettingsView:
    version = _version(if_match)
    try:
        _coordinator(request).save(
            PERSONA_SECTION,
            lambda: _personas(request).activate(body.id),
            version=version,
        )
    except SettingsVersionConflict as exc:
        return error_response(409, "settings_version_conflict", str(exc))
    except PersonaUnavailableError as exc:
        return error_response(422, PersonaUnavailableError.code, str(exc))
    return build_view(request)


@router.put(
    "/model",
    operation_id="update_settings_model",
    response_model=SettingsView,
    responses={**CONFLICT, 422: {"description": "模型不存在，或不满足该能力的角色要求"}},
)
def update_model(
    request: Request,
    body: ActiveLlmRequest,
    if_match: str | None = Header(default=None),
) -> Response | SettingsView:
    version = _version(if_match)
    selections: dict[str, DefaultModel] = {}
    for capability in CAPABILITY_VARS:
        item = getattr(body, capability)
        if item is not None:
            selections[capability] = DefaultModel(provider=item.provider, model=item.model)
    try:
        _coordinator(request).save(
            LLM_SECTION,
            lambda: _llm(request).set_active_models(selections),
            version=version,
        )
    except SettingsVersionConflict as exc:
        return error_response(409, "settings_version_conflict", str(exc))
    except ModelSelectionError as exc:
        return error_response(422, "llm_selection_invalid", str(exc))
    return build_view(request)


@router.put(
    "/interaction",
    operation_id="update_settings_interaction",
    response_model=SettingsView,
    responses=CONFLICT,
)
def update_interaction(
    request: Request,
    body: InteractionConfigIn,
    if_match: str | None = Header(default=None),
) -> Response | SettingsView:
    version = _version(if_match)
    try:
        _coordinator(request).save(
            INTERACTION_SECTION,
            lambda: _interaction(request).write_config(body.model_dump()),
            version=version,
        )
    except SettingsVersionConflict as exc:
        return error_response(409, "settings_version_conflict", str(exc))
    return build_view(request)
