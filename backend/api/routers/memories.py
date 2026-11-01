from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.errors import error_response
from api.schemas.memories import MemoriesResponse, StructuredMemoryUpdate
from module.memory.index import daily_memory_index
from module.memory.services import MemoryServices

router = APIRouter(prefix="/api", tags=["memories"])


def _services(request: Request) -> MemoryServices:
    services: MemoryServices | None = request.app.state.memories
    if services is None:
        raise HTTPException(status_code=503, detail="记忆服务未配置")
    return services


@router.get(
    "/memories",
    operation_id="list_memories",
    response_model=MemoriesResponse,
    responses={422: {"description": "日期格式不合法"}},
)
def list_memories(request: Request, date: str | None = None) -> Response | MemoriesResponse:
    services = _services(request)
    daily_records = None
    if date is not None:
        try:
            daily_records = services.daily.day_views(date)
        except ValueError as exc:
            return error_response(422, "invalid_date", str(exc))
    return MemoriesResponse(
        structured=services.structured.text(),
        daily_memory=daily_memory_index(services.daily.dates()),
        daily_records=daily_records,
    )


@router.put(
    "/memories/structured",
    operation_id="save_structured_memory",
    response_model=StructuredMemoryUpdate,
)
def save_structured_memory(
    request: Request, body: StructuredMemoryUpdate
) -> StructuredMemoryUpdate:
    services = _services(request)
    services.structured.save(body.text)
    return StructuredMemoryUpdate(text=services.structured.text())
