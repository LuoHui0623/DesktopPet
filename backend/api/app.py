from fastapi import FastAPI

from api.auth import TokenAuthMiddleware
from api.errors import register_error_handlers
from api.routers import system


def create_app(token: str) -> FastAPI:
    app = FastAPI(title="Desktop Pet Backend")
    register_error_handlers(app)
    app.include_router(system.router)
    app.add_middleware(TokenAuthMiddleware, token=token)
    return app
