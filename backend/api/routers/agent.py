from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from api.errors import error_response
from api.schemas.approval import ApprovalAck, ApprovalDecision
from module.agent.approval import RESOLVE_CLOSED, RESOLVE_OK, RESOLVE_UNKNOWN, ApprovalRegistry

router = APIRouter(prefix="/api", tags=["agent"])

RESOLVE_ERRORS = {
    RESOLVE_UNKNOWN: (404, "approval_unknown", "审批项不存在或已过期"),
    RESOLVE_CLOSED: (409, "approval_closed", "该审批项已经决出结果"),
}


def _registry(request: Request) -> ApprovalRegistry:
    registry: ApprovalRegistry | None = request.app.state.approvals
    if registry is None:
        raise HTTPException(status_code=503, detail="审批服务未配置")
    return registry


@router.post(
    "/agent/approvals",
    operation_id="submit_approval",
    response_model=ApprovalAck,
    responses={
        404: {"description": "审批项不存在或已过期"},
        409: {"description": "该审批项已经决出结果"},
        503: {"description": "审批服务未配置"},
    },
)
def submit_approval(request: Request, body: ApprovalDecision) -> Response | ApprovalAck:
    result = _registry(request).resolve(body.approval_id, body.approved)
    if result == RESOLVE_OK:
        return ApprovalAck(approval_id=body.approval_id, approved=body.approved)
    status, code, message = RESOLVE_ERRORS.get(result, (500, "resolve_failed", "审批处理失败"))
    return error_response(status, code, message)
