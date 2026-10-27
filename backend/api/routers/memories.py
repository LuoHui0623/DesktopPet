import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.errors import error_response
from api.schemas.memories import (
    CoreAddRequest,
    CoreMemoriesResponse,
    CoreOrderRequest,
    DailyRecordOut,
    DeleteResponse,
    MemoriesResponse,
    MemoryOut,
    MemoryPatch,
    MemorySourceOut,
    SkippedMemoryOut,
    TemporaryModeRequest,
    TemporaryModeResponse,
)
from service.memory.core_memory import CoreMemoryError
from service.memory.daily_events import display_content
from service.memory.index import daily_memory_index
from service.memory.memory_service import ID_RE, MemoryServiceError
from service.memory.services import MemoryServices

router = APIRouter(prefix="/api", tags=["memories"])

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUS_BY_CODE = {
    "not_found": 404,
    "corrupt": 409,
    "temporary_mode": 409,
    "sensitive": 422,
    "invalid": 422,
}
Category = Literal["profile", "preference", "habit", "event", "interaction"]


def _services(request: Request) -> MemoryServices:
    services: MemoryServices | None = request.app.state.memories
    if services is None:
        raise HTTPException(status_code=503, detail="记忆服务未配置")
    return services


def _error(exc: MemoryServiceError) -> Response:
    status = STATUS_BY_CODE.get(exc.code, 500)
    return error_response(status, exc.code, str(exc))


def _memory_out(memory) -> MemoryOut:
    return MemoryOut(
        id=memory.id,
        category=memory.category,
        title=memory.title,
        content=memory.content,
        importance=memory.importance,
        source=MemorySourceOut(type=memory.source.type, date=memory.source.date),
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


def _reject_entry_id(entry_id: str, action: str) -> Response | None:
    if DATE_RE.match(entry_id):
        return error_response(
            403,
            "daily_memory_readonly",
            f"daily_memory 只读，不能{action}每日对话记录",
        )
    if not ID_RE.match(entry_id):
        return error_response(422, "invalid_memory_id", f"非法的记忆 id：{entry_id}")
    return None


@router.get(
    "/memories",
    operation_id="list_memories",
    response_model=MemoriesResponse,
    responses={422: {"description": "日期格式不合法"}},
)
def list_memories(
    request: Request,
    category: Category | None = None,
    date: str | None = None,
) -> Response | MemoriesResponse:
    services = _services(request)
    listing = services.structured.list(category)
    daily_records = None
    if date is not None:
        try:
            records = services.daily.read_day(date)
        except ValueError as exc:
            return error_response(422, "invalid_date", str(exc))
        daily_records = [
            DailyRecordOut(
                id=record.id,
                date=record.date,
                role=record.role,
                content=display_content(record.role, record.content),
                created_at=record.created_at,
            )
            for record in records
        ]
    return MemoriesResponse(
        temporary_mode=services.daily.temporary_mode(),
        core_ids=services.core.ids(),
        structured=[_memory_out(memory) for memory in listing.items],
        skipped=[SkippedMemoryOut(name=item.name, reason=item.reason) for item in listing.skipped],
        daily_memory=daily_memory_index(services.daily.dates()),
        daily_records=daily_records,
    )


@router.post(
    "/memories/temporary-mode",
    operation_id="set_memory_temporary_mode",
    response_model=TemporaryModeResponse,
)
def set_temporary_mode(request: Request, body: TemporaryModeRequest) -> TemporaryModeResponse:
    services = _services(request)
    services.daily.set_temporary_mode(body.enabled)
    return TemporaryModeResponse(temporary_mode=services.daily.temporary_mode())


@router.get(
    "/memories/{memory_id}",
    operation_id="get_memory",
    response_model=MemoryOut,
    responses={404: {"description": "记忆不存在"}, 409: {"description": "记忆文件已损坏"}},
)
def get_memory(request: Request, memory_id: str) -> Response | MemoryOut:
    services = _services(request)
    rejected = _reject_entry_id(memory_id, "查看")
    if rejected is not None:
        return rejected
    try:
        memory = services.structured.get(memory_id)
    except MemoryServiceError as exc:
        return _error(exc)
    return _memory_out(memory)


@router.patch(
    "/memories/{memory_id}",
    operation_id="update_memory",
    response_model=MemoryOut,
    responses={
        403: {"description": "daily_memory 只读"},
        404: {"description": "记忆不存在"},
        409: {"description": "临时模式开启，或记忆文件已损坏"},
        422: {"description": "字段不合法，或包含敏感信息"},
    },
)
def update_memory(request: Request, memory_id: str, body: MemoryPatch) -> Response | MemoryOut:
    services = _services(request)
    rejected = _reject_entry_id(memory_id, "修改")
    if rejected is not None:
        return rejected
    changes = body.model_dump(exclude_none=True)
    if not changes:
        return error_response(422, "no_changes", "未提供任何可修改的字段")
    try:
        memory = services.writer.revise(memory_id, **changes)
    except MemoryServiceError as exc:
        return _error(exc)
    return _memory_out(memory)


@router.delete(
    "/memories/{memory_id}",
    operation_id="delete_memory",
    response_model=DeleteResponse,
    responses={
        403: {"description": "daily_memory 只读"},
        404: {"description": "记忆不存在"},
    },
)
def delete_memory(request: Request, memory_id: str) -> Response | DeleteResponse:
    services = _services(request)
    rejected = _reject_entry_id(memory_id, "删除")
    if rejected is not None:
        return rejected
    try:
        services.structured.delete(memory_id)
    except MemoryServiceError as exc:
        return _error(exc)
    return DeleteResponse(id=memory_id, deleted=True)


def _core_error(exc: CoreMemoryError) -> Response:
    return error_response(422, "core_memory_invalid", str(exc))


def _invalid_core_id(memory_id: str) -> Response | None:
    if not ID_RE.match(memory_id):
        return error_response(422, "invalid_memory_id", f"非法的记忆 id：{memory_id}")
    return None


@router.post(
    "/memories/core",
    operation_id="add_core_memory",
    response_model=CoreMemoriesResponse,
    responses={422: {"description": "记忆不存在、已在典藏中、或典藏已满"}},
)
def add_core_memory(request: Request, body: CoreAddRequest) -> Response | CoreMemoriesResponse:
    services = _services(request)
    rejected = _invalid_core_id(body.memory_id)
    if rejected is not None:
        return rejected
    try:
        core_ids = services.core.add(body.memory_id)
    except CoreMemoryError as exc:
        return _core_error(exc)
    return CoreMemoriesResponse(core_ids=core_ids)


@router.delete(
    "/memories/core/{memory_id}",
    operation_id="remove_core_memory",
    response_model=CoreMemoriesResponse,
    responses={422: {"description": "记忆不在典藏中，或 id 不合法"}},
)
def remove_core_memory(request: Request, memory_id: str) -> Response | CoreMemoriesResponse:
    services = _services(request)
    rejected = _invalid_core_id(memory_id)
    if rejected is not None:
        return rejected
    try:
        core_ids = services.core.remove(memory_id)
    except CoreMemoryError as exc:
        return _core_error(exc)
    return CoreMemoriesResponse(core_ids=core_ids)


@router.put(
    "/memories/core/order",
    operation_id="reorder_core_memory",
    response_model=CoreMemoriesResponse,
    responses={422: {"description": "列表不完整、有重复或新增了条目"}},
)
def reorder_core_memory(
    request: Request, body: CoreOrderRequest
) -> Response | CoreMemoriesResponse:
    services = _services(request)
    try:
        core_ids = services.core.reorder(body.memory_ids)
    except CoreMemoryError as exc:
        return _core_error(exc)
    return CoreMemoriesResponse(core_ids=core_ids)
