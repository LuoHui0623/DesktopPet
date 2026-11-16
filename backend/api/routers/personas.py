from fastapi import APIRouter, HTTPException, Request

from agent.persona.persona_service import PersonaService
from api.schemas.personas import PersonaItemOut, PersonasResponse

router = APIRouter(prefix="/api", tags=["personas"])


def _service(request: Request) -> PersonaService:
    service: PersonaService | None = request.app.state.personas
    if service is None:
        raise HTTPException(status_code=503, detail="人设服务未配置")
    return service


def persona_view(service: PersonaService) -> PersonasResponse:
    """人设分组的读模型：启用人设 + 卡片清单（含可用性与原因）。"""
    return PersonasResponse(
        active=service.active_id(),
        personas=[
            PersonaItemOut(
                id=item.id,
                name=item.name,
                available=item.available,
                reason=item.reason,
                active=item.active,
            )
            for item in service.listing()
        ],
    )


@router.get(
    "/personas",
    operation_id="list_personas",
    response_model=PersonasResponse,
)
def list_personas(request: Request) -> PersonasResponse:
    return persona_view(_service(request))
