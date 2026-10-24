from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.errors import error_response
from api.schemas.llm import (
    DefaultInfo,
    LlmProvidersResponse,
    ModelInfo,
    ProviderInfo,
    ReloadResponse,
)
from service.llm import LlmService

router = APIRouter(prefix="/api/llm", tags=["llm"])


def _service(request: Request) -> LlmService:
    service: LlmService | None = request.app.state.llm_service
    if service is None:
        raise HTTPException(status_code=503, detail="LLM 服务未配置")
    return service


@router.get(
    "/providers",
    operation_id="list_llm_providers",
    response_model=LlmProvidersResponse,
)
async def list_llm_providers(request: Request) -> LlmProvidersResponse:
    service = _service(request)
    settings = service.current()
    statuses = {status.name: status for status in service.providers()}

    providers = []
    for provider in settings.providers.values():
        status = statuses[provider.name]
        models = [
            ModelInfo(name=model.name, id=model.id, api=model.api, roles=list(model.roles))
            for model in provider.models.values()
        ]
        providers.append(
            ProviderInfo(
                name=provider.name,
                display_name=provider.display_name,
                available=status.available,
                reason=status.reason,
                models=models,
            )
        )

    defaults = {
        capability: DefaultInfo(provider=ref.provider, model=ref.model)
        for capability, ref in settings.defaults.items()
    }
    return LlmProvidersResponse(
        version=settings.version,
        providers=providers,
        defaults=defaults,
        warnings=list(settings.warnings),
        last_reload_errors=service.last_errors,
    )


@router.post(
    "/reload",
    operation_id="reload_llm_config",
    response_model=ReloadResponse,
    responses={422: {"description": "llm.yaml 或 .env 校验失败，旧配置保持生效"}},
)
async def reload_llm_config(request: Request) -> Response | ReloadResponse:
    service = _service(request)
    result = service.reload()
    if not result.ok:
        return error_response(422, "llm_config_invalid", "；".join(result.errors))
    settings = service.current()
    return ReloadResponse(ok=True, version=settings.version, warnings=list(settings.warnings))
