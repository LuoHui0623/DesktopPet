from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from agent.memory.contracts import STATES, MemoryEntry
from agent.memory.entry_service import MemoryEntryError
from agent.memory.index import daily_memory_index
from agent.memory.services import MemoryServices
from api.errors import error_response
from api.schemas.memories import (
    EntryAddResult,
    EntryCreateRequest,
    EntryDisableRequest,
    EntryListResponse,
    EntryUpdateRequest,
    MemoriesResponse,
    MemoryEntryView,
    StructuredMemoryUpdate,
    TagListResponse,
    TagMergePreviewResponse,
    TagMergeRequest,
    TagMergeResponse,
    TagPathChangeView,
)

router = APIRouter(prefix="/api", tags=["memories"])

# 条目服务错误码 → HTTP 状态：冲突 409、不存在 404、格式/敏感 422（与既有路由口径一致）。
ENTRY_ERROR_STATUS = {"conflict": 409, "not_found": 404, "invalid": 422, "sensitive": 422}

# 合并/改名两个端点共用的错误响应说明。
_MERGE_RESPONSES = {
    404: {"description": "没有条目使用源 tag"},
    422: {"description": "源与目标相同或格式不合法"},
}


def _services(request: Request) -> MemoryServices:
    services: MemoryServices | None = request.app.state.memories
    if services is None:
        raise HTTPException(status_code=503, detail="记忆服务未配置")
    return services


def _entry_error(exc: MemoryEntryError) -> Response:
    return error_response(ENTRY_ERROR_STATUS.get(exc.code, 422), exc.code, str(exc))


def _entry_view(entry: MemoryEntry) -> MemoryEntryView:
    return MemoryEntryView(
        id=entry.id,
        content=entry.content,
        tags=list(entry.tags),
        status_state=entry.status_state,
        status_reason=entry.status_reason,
        revision=entry.revision,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


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
        structured=services.entries.structured_text(),
        daily_memory=daily_memory_index(services.daily.dates()),
        daily_records=daily_records,
    )


@router.put(
    "/memories/structured",
    operation_id="save_structured_memory",
)
def save_structured_memory(request: Request, body: StructuredMemoryUpdate) -> Response:
    """结构化记忆已改为条目存储，整篇覆盖写入口关闭（Q8）；人工编辑走条目级接口。"""
    _services(request)
    return error_response(
        409,
        "read_only",
        "结构化记忆已改为条目存储，不能整篇覆盖；请使用条目级编辑接口或 memory 工具",
    )


@router.get(
    "/memories/entries",
    operation_id="list_memory_entries",
    response_model=EntryListResponse,
    responses={422: {"description": "状态过滤值不合法"}},
)
def list_memory_entries(
    request: Request, status: str | None = None
) -> Response | EntryListResponse:
    """列出条目（默认含停用，供设置页可见并标注原因）；可按 status 过滤。"""
    services = _services(request)
    if status is not None and status not in STATES:
        return error_response(422, "invalid", f"非法状态过滤值：{status!r}")
    entries = services.entries.load(status=status, limit=None)
    return EntryListResponse(entries=[_entry_view(entry) for entry in entries])


@router.post(
    "/memories/entries",
    operation_id="create_memory_entry",
    response_model=EntryAddResult,
    responses={422: {"description": "正文/标签格式不合法或含敏感信息"}},
)
def create_memory_entry(request: Request, body: EntryCreateRequest) -> Response | EntryAddResult:
    services = _services(request)
    try:
        result = services.entries.add(body)
    except MemoryEntryError as exc:
        return _entry_error(exc)
    return EntryAddResult(
        entry=_entry_view(result.entry),
        reused=result.reused,
        new_tags=list(result.new_tags),
    )


@router.put(
    "/memories/entries/{entry_id}",
    operation_id="update_memory_entry",
    response_model=MemoryEntryView,
    responses={
        404: {"description": "条目不存在"},
        409: {"description": "revision 冲突，需刷新后重试"},
        422: {"description": "正文/标签格式不合法或含敏感信息"},
    },
)
def update_memory_entry(
    request: Request, entry_id: int, body: EntryUpdateRequest
) -> Response | MemoryEntryView:
    services = _services(request)
    try:
        entry = services.entries.update(entry_id, body, expected_revision=body.expected_revision)
    except MemoryEntryError as exc:
        return _entry_error(exc)
    return _entry_view(entry)


@router.post(
    "/memories/entries/{entry_id}/enable",
    operation_id="enable_memory_entry",
    response_model=MemoryEntryView,
    responses={404: {"description": "条目不存在"}},
)
def enable_memory_entry(request: Request, entry_id: int) -> Response | MemoryEntryView:
    services = _services(request)
    try:
        entry = services.entries.enable(entry_id)
    except MemoryEntryError as exc:
        return _entry_error(exc)
    return _entry_view(entry)


@router.post(
    "/memories/entries/{entry_id}/disable",
    operation_id="disable_memory_entry",
    response_model=MemoryEntryView,
    responses={404: {"description": "条目不存在"}, 422: {"description": "弃用原因不能为空"}},
)
def disable_memory_entry(
    request: Request, entry_id: int, body: EntryDisableRequest
) -> Response | MemoryEntryView:
    services = _services(request)
    try:
        entry = services.entries.disable(entry_id, body.reason)
    except MemoryEntryError as exc:
        return _entry_error(exc)
    return _entry_view(entry)


@router.get(
    "/memories/tags",
    operation_id="list_memory_tags",
    response_model=TagListResponse,
)
def list_memory_tags(request: Request) -> TagListResponse:
    """当前全部 tag 写法，供设置页人工自选 merge 的 source/target（Q33：不自动建议）。"""
    services = _services(request)
    return TagListResponse(tags=services.entries.tags())


@router.post(
    "/memories/tags/merge/preview",
    operation_id="preview_tag_merge",
    response_model=TagMergePreviewResponse,
    responses=_MERGE_RESPONSES,
)
def preview_tag_merge(
    request: Request, body: TagMergeRequest
) -> Response | TagMergePreviewResponse:
    """合并/改名前的逐条有序路径 before→after 预览（Q27/第 29 条）：只读、不写库。"""
    services = _services(request)
    try:
        preview = services.entries.merge_preview(body.source, body.target)
    except MemoryEntryError as exc:
        return _entry_error(exc)
    return TagMergePreviewResponse(
        source=preview.source,
        target=preview.target,
        target_existed=preview.target_existed,
        affected=preview.affected,
        changes=[
            TagPathChangeView(
                entry_id=change.entry_id,
                content=change.content,
                before=list(change.before),
                after=list(change.after),
            )
            for change in preview.changes
        ],
    )


@router.post(
    "/memories/tags/merge",
    operation_id="merge_memory_tags",
    response_model=TagMergeResponse,
    responses=_MERGE_RESPONSES,
)
def merge_memory_tags(request: Request, body: TagMergeRequest) -> Response | TagMergeResponse:
    """人工 tag 治理：target 存在即合并、不存在即改名；一次事务执行并递增受影响条目 revision。"""
    services = _services(request)
    try:
        result = services.entries.merge(body.source, body.target)
    except MemoryEntryError as exc:
        return _entry_error(exc)
    return TagMergeResponse(
        source=result.source,
        target=result.target,
        affected=result.affected,
        target_existed=result.target_existed,
    )
