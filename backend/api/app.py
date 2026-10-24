from collections.abc import Callable

from fastapi import FastAPI

from api.auth import TokenAuthMiddleware
from api.errors import register_error_handlers
from api.routers import system


def create_app(
    token: str,
    request_shutdown: Callable[[str], bool] | None = None,
) -> FastAPI:
    app = FastAPI(title="Desktop Pet Backend")
    app.state.request_shutdown = request_shutdown or (lambda reason: False)
    register_error_handlers(app)
    app.include_router(system.router)
    app.add_middleware(TokenAuthMiddleware, token=token)
    return app
