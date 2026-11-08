from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from starlette.middleware.cors import CORSMiddleware

from api.auth import TokenAuthMiddleware
from api.errors import register_error_handlers
from api.event_publisher import BusEventPublisher
from api.routers import (
    agent,
    avatars,
    chat,
    interaction,
    llm,
    menu,
    perception,
    personas,
    scheduler,
    settings,
    system,
    ws,
)
from api.routers import memories as memory_routes
from api.schemas.events import event_component_schemas
from infra.events.bus import EventBus
from module.agent.approval import PROCESS_EXIT_REASON, ApprovalRegistry
from module.avatar.avatar_service import AvatarService
from module.chat.chat_service import ChatService
from module.interaction.interaction_service import InteractionService
from module.llm import LlmService
from module.memory.services import MemoryServices
from module.menu.menu_service import MenuService
from module.persona.persona_service import PersonaService
from service.perception import PerceptionRuntime, PerceptionService


def create_app(
    token: str,
    request_shutdown: Callable[[str], bool] | None = None,
    llm_service: LlmService | None = None,
    event_bus: EventBus | None = None,
    event_publisher: BusEventPublisher | None = None,
    chat_service: ChatService | None = None,
    memories: MemoryServices | None = None,
    persona_service: PersonaService | None = None,
    approvals: ApprovalRegistry | None = None,
    perception_service: PerceptionService | None = None,
    perception_runtime: PerceptionRuntime | None = None,
    avatar_service: AvatarService | None = None,
    interaction_service: InteractionService | None = None,
    menu_service: MenuService | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if perception_runtime is not None:
            await perception_runtime.start()
        try:
            yield
        finally:
            if perception_runtime is not None:
                await perception_runtime.stop()
            # 进程退出时唤醒所有挂起等待，避免后台线程把关闭流程拖住。
            if approvals is not None:
                approvals.cancel_all(PROCESS_EXIT_REASON)

    app = FastAPI(title="Desktop Pet Backend", lifespan=lifespan)
    app.state.request_shutdown = request_shutdown or (lambda reason: False)
    app.state.llm_service = llm_service
    app.state.event_bus = event_bus or EventBus()
    app.state.event_publisher = event_publisher or BusEventPublisher(app.state.event_bus)
    app.state.chat_service = chat_service
    app.state.memories = memories
    app.state.personas = persona_service
    app.state.approvals = approvals
    app.state.perception = perception_service
    app.state.perception_runtime = perception_runtime
    app.state.scheduler = perception_service.scheduler if perception_service else None
    app.state.avatars = avatar_service
    app.state.interaction = interaction_service
    app.state.menu = menu_service
    register_error_handlers(app)
    app.include_router(system.router)
    app.include_router(llm.router)
    app.include_router(chat.router)
    app.include_router(agent.router)
    app.include_router(memory_routes.router)
    app.include_router(personas.router)
    app.include_router(perception.router)
    app.include_router(interaction.router)
    app.include_router(scheduler.router)
    app.include_router(settings.router)
    app.include_router(avatars.router)
    app.include_router(menu.router)
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
