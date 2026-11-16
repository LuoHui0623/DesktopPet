from fastapi import APIRouter, HTTPException, Request

from agent.capabilities.perception.gate.scheduler import SchedulerConfig, SchedulerRuntime
from api.schemas.scheduler import (
    SchedulerConfigView,
    SchedulerLedgerView,
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
