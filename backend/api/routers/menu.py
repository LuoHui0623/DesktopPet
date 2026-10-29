from fastapi import APIRouter, HTTPException, Request

from api.schemas.menu import MenuItemOut, MenuResponse
from module.menu.contracts import MenuItem
from module.menu.menu_service import MenuService

router = APIRouter(prefix="/api", tags=["menu"])


def _service(request: Request) -> MenuService:
    service: MenuService | None = request.app.state.menu
    if service is None:
        raise HTTPException(status_code=503, detail="菜单服务未配置")
    return service


def _item(item: MenuItem) -> MenuItemOut:
    return MenuItemOut(
        id=item.id,
        label=item.label,
        enabled=item.enabled,
        checked=item.checked,
        children=[_item(child) for child in item.children],
    )


@router.get("/menu", operation_id="get_menu", response_model=MenuResponse)
def get_menu(request: Request) -> MenuResponse:
    return MenuResponse(items=[_item(item) for item in _service(request).menu().items])
