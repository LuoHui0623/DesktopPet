from fastapi import APIRouter, HTTPException, Request

from api.routers.settings import interaction_view
from api.schemas.interaction import InteractionRequest, InteractionResponse
from api.schemas.settings import InteractionConfigView
from module.interaction.interaction_service import InteractionService

router = APIRouter(prefix="/api/interaction", tags=["interaction"])


def _service(request: Request) -> InteractionService:
    service: InteractionService | None = request.app.state.interaction
    if service is None:
        raise HTTPException(status_code=503, detail="互动服务未配置")
    return service


@router.get(
    "/config",
    operation_id="get_interaction_config",
    response_model=InteractionConfigView,
)
def get_interaction_config(request: Request) -> InteractionConfigView:
    return interaction_view(_service(request))


@router.post(
    "",
    operation_id="report_interaction",
    response_model=InteractionResponse,
    responses={503: {"description": "互动服务未配置"}},
)
def report_interaction(request: Request, body: InteractionRequest) -> InteractionResponse:
    outcome = _service(request).record(body.kind)
    return InteractionResponse(accepted=outcome.accepted, reason=outcome.reason)
