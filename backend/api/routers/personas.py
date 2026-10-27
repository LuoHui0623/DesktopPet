from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.errors import error_response
from api.schemas.personas import PersonaActivateRequest, PersonaItemOut, PersonasResponse
from service.persona.persona_service import PersonaService, PersonaUnavailableError

router = APIRouter(prefix="/api", tags=["personas"])


def _service(request: Request) -> PersonaService:
    service: PersonaService | None = request.app.state.personas
    if service is None:
        raise HTTPException(status_code=503, detail="人设服务未配置")
    return service


def _listing(service: PersonaService) -> PersonasResponse:
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
    return _listing(_service(request))


@router.put(
    "/personas/active",
    operation_id="activate_persona",
    response_model=PersonasResponse,
    responses={422: {"description": "人设不存在，或解析失败不可用"}},
)
def activate_persona(request: Request, body: PersonaActivateRequest) -> Response | PersonasResponse:
    service = _service(request)
    try:
        service.activate(body.id)
    except PersonaUnavailableError as exc:
        return error_response(422, PersonaUnavailableError.code, str(exc))
    return _listing(service)
