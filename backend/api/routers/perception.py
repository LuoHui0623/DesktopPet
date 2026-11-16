from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from agent.capabilities.perception.collector.service import PerceptionService
from agent.capabilities.perception.events.models import (
    InvalidPerceptionSample,
    PerceptionSnapshot,
    ProcessDecision,
)
from agent.capabilities.perception.gate.settings import SECTION as PERCEPTION_SECTION
from agent.capabilities.perception.gate.settings import PerceptionConfig
from api.errors import error_response
from api.schemas.perception import (
    PerceptionClearResponse,
    PerceptionConfigView,
    PerceptionIngestResponse,
    PerceptionRequest,
    PerceptionStateResponse,
    ProcessDecisionRequest,
)
from app.settings.coordinator import SettingsCoordinator

router = APIRouter(prefix="/api/perception", tags=["perception"])


def _service(request: Request) -> PerceptionService:
    service: PerceptionService | None = request.app.state.perception
    if service is None:
        raise HTTPException(status_code=503, detail="感知服务未配置")
    return service


def _state(snapshot: PerceptionSnapshot) -> PerceptionStateResponse:
    return PerceptionStateResponse(
        enabled=snapshot.enabled,
        foreground_enabled=snapshot.foreground_enabled,
        presence=snapshot.presence,
        busy=snapshot.busy,
        idle_seconds=snapshot.idle_seconds,
        foreground=snapshot.foreground,
        whitelist=list(snapshot.whitelist),
        muted=list(snapshot.muted),
        candidates=list(snapshot.candidates),
    )


@router.post(
    "",
    operation_id="ingest_perception",
    response_model=PerceptionIngestResponse,
    responses={422: {"description": "感知类型或取值不合法"}},
)
def ingest_perception(
    request: Request, body: PerceptionRequest
) -> Response | PerceptionIngestResponse:
    service = _service(request)
    try:
        outcome = service.ingest(body.kind, body.value, body.at)
    except InvalidPerceptionSample as exc:
        return error_response(422, "invalid_perception", str(exc))
    return PerceptionIngestResponse(
        accepted=outcome.accepted,
        reason=outcome.reason,
        candidates=list(outcome.candidates),
    )


@router.get(
    "/state",
    operation_id="get_perception_state",
    response_model=PerceptionStateResponse,
)
def get_perception_state(request: Request) -> PerceptionStateResponse:
    return _state(_service(request).snapshot())


def config_view(config: PerceptionConfig) -> PerceptionConfigView:
    return PerceptionConfigView.of(config)


@router.post(
    "/clear",
    operation_id="clear_perception_records",
    response_model=PerceptionClearResponse,
)
def clear_perception_records(request: Request) -> PerceptionClearResponse:
    """清空内存中的感知缓冲；感知不落库，因此无需数据库操作。"""
    _service(request).clear()
    return PerceptionClearResponse(cleared=True)


@router.post(
    "/processes/decision",
    operation_id="decide_perception_process",
    response_model=PerceptionStateResponse,
    responses={422: {"description": "进程名不合法"}},
)
def decide_perception_process(
    request: Request, body: ProcessDecisionRequest
) -> Response | PerceptionStateResponse:
    service = _service(request)
    try:
        snapshot = service.decide_process(body.exe, ProcessDecision(body.decision))
    except InvalidPerceptionSample as exc:
        return error_response(422, "invalid_perception", str(exc))
    # 名单也是配置：决策后立即投射进快照，避免重启丢失（`R8`）。
    coordinator: SettingsCoordinator | None = request.app.state.settings
    if coordinator is not None:
        coordinator.project(PERCEPTION_SECTION)
    return _state(snapshot)
