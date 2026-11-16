from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from api.schemas.avatars import AvatarsResponse
from app.avatar.avatar_service import AvatarNotFoundError, AvatarService
from app.avatar.contracts import AvatarCatalog

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
