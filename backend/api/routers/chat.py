from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from api.errors import error_response
from api.event_publisher import source_out
from api.schemas.chat import (
    ChatHistoryItem,
    ChatHistoryResponse,
    ChatReplyResponse,
    ChatRequest,
)
from service.chat.chat_service import ChatError, ChatService
from service.memory.daily_events import display_content

router = APIRouter(prefix="/api", tags=["chat"])

CHAT_ERROR_STATUS = {
    "empty_input": 422,
    "model_unavailable": 503,
    "model_failed": 502,
    "sentence_too_long": 502,
    "invalid_output": 502,
    "persona_unavailable": 503,
}


def _service(request: Request) -> ChatService:
    service: ChatService | None = request.app.state.chat_service
    if service is None:
        raise HTTPException(status_code=503, detail="对话服务未配置")
    return service


@router.post(
    "/chat",
    operation_id="send_chat",
    response_model=ChatReplyResponse,
    responses={
        422: {"description": "消息为空"},
        502: {"description": "模型调用失败，或回复句长超限"},
        503: {"description": "对话模型不可用"},
    },
)
def send_chat(request: Request, body: ChatRequest) -> Response | ChatReplyResponse:
    service = _service(request)
    try:
        reply = service.reply(body.text)
    except ChatError as exc:
        status = CHAT_ERROR_STATUS.get(exc.code, 500)
        return error_response(status, exc.code, str(exc))
    return ChatReplyResponse(
        message_id=reply.message_id,
        text=reply.text,
        recorded=reply.recorded,
        sources=[source_out(source) for source in reply.sources],
    )


@router.get(
    "/chat/history",
    operation_id="list_chat_history",
    response_model=ChatHistoryResponse,
    responses={422: {"description": "日期格式不合法"}},
)
def list_chat_history(
    request: Request,
    date_from: str | None = None,
    date_to: str | None = None,
    keyword: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Response | ChatHistoryResponse:
    service = _service(request)
    try:
        records, has_more = service.history(
            keyword=keyword,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        return error_response(422, "invalid_date", str(exc))
    items = [
        ChatHistoryItem(
            id=record.id,
            date=record.date,
            role=record.role,
            content=display_content(record.role, record.content),
            created_at=record.created_at,
        )
        for record in records
    ]
    return ChatHistoryResponse(items=items, has_more=has_more)
