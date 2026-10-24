from collections.abc import Callable

from fastapi import FastAPI

from api.auth import TokenAuthMiddleware
from api.errors import register_error_handlers
from api.routers import llm, system
from service.llm import LlmService


def create_app(
    token: str,
    request_shutdown: Callable[[str], bool] | None = None,
    llm_service: LlmService | None = None,
) -> FastAPI:
    app = FastAPI(title="Desktop Pet Backend")
    app.state.request_shutdown = request_shutdown or (lambda reason: False)
    app.state.llm_service = llm_service
    register_error_handlers(app)
    app.include_router(system.router)
    app.include_router(llm.router)
    app.add_middleware(TokenAuthMiddleware, token=token)
    return app
