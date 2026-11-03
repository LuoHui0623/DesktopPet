from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from api.errors import error_response
from api.schemas.avatars import (
    ActivateAvatarIn,
    AvatarsResponse,
)
from module.avatar.avatar_service import AvatarNotFoundError, AvatarService
from module.avatar.contracts import AvatarCatalog

router = APIRouter(prefix="/api", tags=["avatars"])


def _service(request: Request) -> AvatarService:
    service: AvatarService | None = request.app.state.avatars
    if service is None:
        raise HTTPException(status_code=503, detail="形象服务未配置")
    return service


def catalog_view(catalog: AvatarCatalog) -> AvatarsResponse:
    return AvatarsResponse.of(catalog)


@router.get(
    "/avatars",
    operation_id="list_avatars",
    response_model=AvatarsResponse,
)
def list_avatars(request: Request) -> AvatarsResponse:
    return catalog_view(_service(request).catalog())


@router.put(
    "/avatars/active",
    operation_id="activate_avatar",
    response_model=AvatarsResponse,
    responses={404: {"description": "形象包不存在"}},
)
def activate_avatar(request: Request, body: ActivateAvatarIn) -> Response | AvatarsResponse:
    try:
        catalog = _service(request).activate(body.id)
    except AvatarNotFoundError:
        return error_response(404, "avatar_not_found", f"形象包不存在：{body.id}")
    return catalog_view(catalog)


@router.get(
    "/avatars/{persona_id}/{pack_id}/{path:path}",
    operation_id="get_avatar_asset",
    include_in_schema=False,
)
def get_avatar_asset(request: Request, persona_id: str, pack_id: str, path: str) -> FileResponse:
    try:
        asset = _service(request).asset_path(persona_id, pack_id, path)
    except AvatarNotFoundError:
        raise HTTPException(status_code=404, detail="形象资源不存在") from None
    return FileResponse(asset)
