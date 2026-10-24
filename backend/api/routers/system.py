from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", operation_id="health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/shutdown", operation_id="shutdown", status_code=202)
async def shutdown(request: Request) -> dict[str, str]:
    accepted = request.app.state.request_shutdown("HTTP 接口")
    return {"status": "shutting_down" if accepted else "already_shutting_down"}
