from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.errors import error_response
from api.routers.avatars import catalog_view
from api.routers.perception import config_view as perception_config_view
from api.routers.scheduler import config_view as scheduler_config_view
from api.schemas.avatars import ActivateAvatarIn
from api.schemas.perception import PerceptionConfigIn
from api.schemas.scheduler import SchedulerConfigIn
from api.schemas.settings import InteractionConfigIn, InteractionConfigView, SettingsView
from module.avatar.avatar_service import AvatarNotFoundError, AvatarService
from module.interaction.interaction_service import InteractionService
from service.perception import (
    InvalidPerceptionConfig,
    InvalidSchedulerConfig,
    PerceptionService,
    SchedulerConfig,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _perception(request: Request) -> PerceptionService:
    service: PerceptionService | None = request.app.state.perception
    if service is None:
        raise HTTPException(status_code=503, detail="感知服务未配置")
    return service


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


def interaction_view(service: InteractionService) -> InteractionConfigView:
    return InteractionConfigView.of(service.config())


def build_view(request: Request) -> SettingsView:
    perception = _perception(request)
    return SettingsView(
        perception=perception_config_view(perception.config()),
        proactive=scheduler_config_view(perception.scheduler.config()),
        avatar=catalog_view(_avatars(request).catalog()),
        interaction=interaction_view(_interaction(request)),
    )


@router.get("", operation_id="get_settings", response_model=SettingsView)
def get_settings(request: Request) -> SettingsView:
    return build_view(request)


@router.put(
    "/perception",
    operation_id="update_settings_perception",
    response_model=SettingsView,
    responses={422: {"description": "感知配置取值不合法"}},
)
def update_perception(request: Request, body: PerceptionConfigIn) -> Response | SettingsView:
    try:
        _perception(request).write_config(
            enabled=body.enabled,
            foreground_enabled=body.foreground_enabled,
            idle_away_minutes=body.idle_away_minutes,
            sit_minutes=body.sit_minutes,
        )
    except InvalidPerceptionConfig as exc:
        return error_response(422, "invalid_perception_config", str(exc))
    return build_view(request)


@router.put(
    "/proactive",
    operation_id="update_settings_proactive",
    response_model=SettingsView,
    responses={422: {"description": "调度配置取值不合法"}},
)
def update_proactive(request: Request, body: SchedulerConfigIn) -> Response | SettingsView:
    config = SchedulerConfig(
        enabled=body.enabled,
        min_interval_minutes=body.min_interval_minutes,
        max_per_day=body.max_per_day,
        dnd_start=body.dnd_start,
        dnd_end=body.dnd_end,
    )
    try:
        _perception(request).scheduler.write_config(config)
    except InvalidSchedulerConfig as exc:
        return error_response(422, "invalid_scheduler_config", str(exc))
    return build_view(request)


@router.put(
    "/avatar",
    operation_id="update_settings_avatar",
    response_model=SettingsView,
    responses={404: {"description": "形象包不存在"}},
)
def update_avatar(request: Request, body: ActivateAvatarIn) -> Response | SettingsView:
    try:
        _avatars(request).activate(body.id)
    except AvatarNotFoundError:
        return error_response(404, "avatar_not_found", f"形象包不存在：{body.id}")
    return build_view(request)


@router.put(
    "/interaction",
    operation_id="update_settings_interaction",
    response_model=SettingsView,
)
def update_interaction(request: Request, body: InteractionConfigIn) -> SettingsView:
    _interaction(request).write_config(body.model_dump())
    return build_view(request)
