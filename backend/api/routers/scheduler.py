from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.errors import error_response
from api.schemas.scheduler import (
    SchedulerConfigIn,
    SchedulerConfigView,
    SchedulerLedgerView,
)
from service.perception import (
    InvalidSchedulerConfig,
    SchedulerConfig,
    SchedulerRuntime,
)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


def _service(request: Request) -> SchedulerRuntime:
    runtime: SchedulerRuntime | None = request.app.state.scheduler
    if runtime is None:
        raise HTTPException(status_code=503, detail="调度服务未配置")
    return runtime


def config_view(config: SchedulerConfig) -> SchedulerConfigView:
    return SchedulerConfigView.of(config)


@router.get(
    "/config",
    operation_id="get_scheduler_config",
    response_model=SchedulerConfigView,
)
def get_scheduler_config(request: Request) -> SchedulerConfigView:
    return config_view(_service(request).config())


@router.put(
    "/config",
    operation_id="update_scheduler_config",
    response_model=SchedulerConfigView,
    responses={422: {"description": "调度配置取值不合法"}},
)
def update_scheduler_config(
    request: Request, body: SchedulerConfigIn
) -> Response | SchedulerConfigView:
    config = SchedulerConfig(
        enabled=body.enabled,
        min_interval_minutes=body.min_interval_minutes,
        max_per_day=body.max_per_day,
        dnd_start=body.dnd_start,
        dnd_end=body.dnd_end,
    )
    try:
        saved = _service(request).write_config(config)
    except InvalidSchedulerConfig as exc:
        return error_response(422, "invalid_scheduler_config", str(exc))
    return config_view(saved)


@router.get(
    "/ledger",
    operation_id="get_scheduler_ledger",
    response_model=SchedulerLedgerView,
)
def get_scheduler_ledger(request: Request) -> SchedulerLedgerView:
    ledger = _service(request).ledger()
    return SchedulerLedgerView(
        date=ledger.day.isoformat(),
        topics=dict(ledger.topics),
        total=ledger.total,
    )
