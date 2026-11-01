from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from api.errors import error_response
from api.event_publisher import reply_message_out, source_out
from api.schemas.chat import ChatHistoryResponse, ChatReplyResponse, ChatRequest
from module.chat.chat_service import ChatError, ChatService
from module.chat.chat_service import ChatRequest as ServiceChatRequest

router = APIRouter(prefix="/api", tags=["chat"])

CHAT_ERROR_STATUS = {
    "empty_input": 422,
    "model_unavailable": 503,
    "model_failed": 502,
    "sentence_too_long": 502,
    "invalid_output": 502,
    "persona_unavailable": 503,
    "context_overflow": 413,
    "token_count_failed": 503,
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
        413: {"description": "上下文超出模型窗口"},
        422: {"description": "消息为空"},
        502: {"description": "模型调用失败，或回复句长超限"},
        503: {"description": "对话模型不可用，或分词计数失败"},
    },
)
def send_chat(request: Request, body: ChatRequest) -> Response | ChatReplyResponse:
    service = _service(request)
    try:
        reply = service.chat(ServiceChatRequest(user_text=body.text))
    except ChatError as exc:
        status = CHAT_ERROR_STATUS.get(exc.code, 500)
        return error_response(status, exc.code, str(exc))
    return ChatReplyResponse(
        message_id=reply.message_id,
        text=reply.text,
        recorded=reply.recorded,
        sources=[source_out(source) for source in reply.sources],
        messages=[reply_message_out(message) for message in reply.messages],
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
    return ChatHistoryResponse(items=records, has_more=has_more)
