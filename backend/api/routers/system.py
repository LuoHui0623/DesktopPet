from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", operation_id="health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
