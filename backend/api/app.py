from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from starlette.middleware.cors import CORSMiddleware

from api.auth import TokenAuthMiddleware
from api.errors import register_error_handlers
from api.routers import chat, llm, personas, system, ws
from api.routers import memories as memory_routes
from api.schemas.events import event_component_schemas
from infra.events.bus import EventBus
from service.chat.chat_service import ChatService
from service.llm import LlmService
from service.memory.services import MemoryServices
from service.persona.persona_service import PersonaService


def create_app(
    token: str,
    request_shutdown: Callable[[str], bool] | None = None,
    llm_service: LlmService | None = None,
    event_bus: EventBus | None = None,
    chat_service: ChatService | None = None,
    memories: MemoryServices | None = None,
    persona_service: PersonaService | None = None,
) -> FastAPI:
    app = FastAPI(title="Desktop Pet Backend")
    app.state.request_shutdown = request_shutdown or (lambda reason: False)
    app.state.llm_service = llm_service
    app.state.event_bus = event_bus or EventBus()
    app.state.chat_service = chat_service
    app.state.memories = memories
    app.state.personas = persona_service
    register_error_handlers(app)
    app.include_router(system.router)
    app.include_router(llm.router)
    app.include_router(chat.router)
    app.include_router(memory_routes.router)
    app.include_router(personas.router)
    app.include_router(ws.router)
    app.add_middleware(TokenAuthMiddleware, token=token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(RENDERER_ORIGINS),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Pet-Token"],
    )
    _include_event_schemas(app)
    return app


RENDERER_ORIGINS = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
)


def _include_event_schemas(app: FastAPI) -> None:
    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                routes=app.routes,
                tags=app.openapi_tags,
            )
            schema.setdefault("components", {}).setdefault("schemas", {}).update(
                event_component_schemas()
            )
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
