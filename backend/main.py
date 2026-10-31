import logging
import socket
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn

from api.app import create_app
from api.event_publisher import BusEventPublisher
from config.persona import scan_cards
from config.settings import (
    ModelSettingsError,
    ModelSettingsSource,
    ServiceSettings,
    ServiceSettingsError,
    deprecated_scheduler_values,
    load_service_settings,
)
from constants.path import AVATAR_SOURCE_DIR
from infra.events.bus import EventBus
from infra.lifecycle import ShutdownController, watch_stdin
from infra.lock import InstanceLockError, SingleInstanceLock
from infra.logging import attach_db_logging, setup_logging
from infra.store.db import connect
from infra.store.migrate import MigrationError, migrate
from module.agent.approval import ApprovalRegistry
from module.agent.orchestrator import Orchestrator
from module.agent.prompt import (
    CapabilityDescription,
    PromptError,
    PromptTemplates,
    default_resources,
    load_capabilities,
    load_templates,
    merge_capabilities,
)
from module.agent.repository import DbAgentRuns
from module.agent.skills import (
    SkillRegistry,
    draft_note_skill,
    memory_retrieve_skill,
    memory_write_skill,
)
from module.agent.subagent import SubAgent
from module.agent.tools.explore import make_explore_tools
from module.agent.tools.file import make_file_tools
from module.agent.tools.memory import make_memory_write_tools
from module.agent.workspace import prepare_workspace
from module.avatar.avatar_service import AvatarService
from module.chat.activity import ChatActivity
from module.chat.chat_service import ChatService
from module.interaction.interaction_service import InteractionService
from module.llm import LlmService
from module.llm.repository import db_usage_sink
from module.memory.daily_log_service import DailyLogService
from module.memory.index import MemoryIndexService
from module.memory.memory_service import (
    MemoryReader,
    MemoryWriter,
    StructuredMemoryDoc,
)
from module.memory.repository import DbDailyLog
from module.memory.services import MemoryServices
from module.menu.menu_service import MenuService
from module.persona.persona_service import PersonaService, PersonaUnavailableError
from module.persona.repository import DbPersonaVersions
from module.proactive import ProactivePipeline
from module.settings.settings_service import SettingsService
from service.perception import PerceptionRuntime, PerceptionService

logger = logging.getLogger("pet")
GRACEFUL_SHUTDOWN_SECONDS = 5


def initialize_database(data_dir: Path) -> list[int]:
    conn = connect(data_dir / "pet.db")
    try:
        return migrate(conn)
    finally:
        conn.close()


def bind_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.listen(socket.SOMAXCONN)
    except OSError:
        sock.close()
        raise
    return sock


def serve(
    settings: ServiceSettings,
    llm_service: LlmService,
    templates: PromptTemplates,
    capabilities: tuple[CapabilityDescription, ...],
) -> int:
    try:
        applied = initialize_database(settings.data_dir)
    except (MigrationError, OSError) as exc:
        logger.error("数据库初始化失败：%s", exc)
        print(f"数据库初始化失败：{exc}", file=sys.stderr)
        return 1
    if applied:
        logger.info("数据库迁移完成，已应用版本：%s", applied)
    else:
        logger.info("数据库已是最新版本，无需迁移")
    db_file = settings.data_dir / "pet.db"
    settings_service = SettingsService(db_file)
    deprecated = deprecated_scheduler_values()
    if deprecated:
        logger.warning(
            ".env 中的 %s 已废弃：勿扰、上限、间隔只在设置页改（存 app_setting），本次启动已忽略",
            "、".join(sorted(deprecated)),
        )
    chat_activity = ChatActivity()
    perception = PerceptionService(
        settings_service,
        timezone=ZoneInfo(settings.timezone),
        chat_active=chat_activity.is_active,
    )
    memory_dir = settings.data_dir / "memory"
    structured_file = memory_dir / "structured.md"
    index_file = memory_dir / "index.json"
    daily_log = DailyLogService(
        DbDailyLog(db_file),
        SettingsService(db_file),
        ZoneInfo(settings.timezone),
    )
    structured_memory = StructuredMemoryDoc(structured_file)
    memory_index = MemoryIndexService(structured_memory.lines, daily_log.dates, index_file)
    structured_memory.add_listener(memory_index.rebuild)
    try:
        memory_index.rebuild()
    except (sqlite3.Error, OSError) as exc:
        logger.error("记忆索引生成失败：%s", exc)
        print(f"记忆索引生成失败：{exc}", file=sys.stderr)
        return 1
    logger.info("记忆索引已生成")
    db_log = attach_db_logging(
        settings.data_dir / "pet.db",
        queue_size=settings.log_queue_size,
        max_wait_seconds=settings.log_max_wait_seconds,
    )

    try:
        sock = bind_socket(settings.host, settings.port)
    except OSError as exc:
        logger.error("无法绑定 %s:%s：%s", settings.host, settings.port, exc)
        print(f"无法绑定 {settings.host}:{settings.port}：{exc}", file=sys.stderr)
        db_log.close()
        return 1

    port = sock.getsockname()[1]
    print(f"PET_PORT={port}", flush=True)
    logger.info("后端已启动，监听 %s:%s", settings.host, port)

    shutdown = ShutdownController()
    event_bus = EventBus()
    memory_writer = MemoryWriter(structured_memory, daily_log)
    memory_reader = MemoryReader(index_file, structured_memory, daily_log)
    memory_services = MemoryServices(
        structured=structured_memory,
        writer=memory_writer,
        daily=daily_log,
    )
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    skills.register(memory_retrieve_skill())
    skills.register(draft_note_skill())
    resource_dir = settings.data_dir / "resources"
    try:
        prepare_workspace(resource_dir)
    except OSError as exc:
        logger.error("资源目录创建失败：%s", exc)
        print(f"资源目录创建失败：{exc}", file=sys.stderr)
        return 1
    subagent = SubAgent(
        llm_service,
        [
            *make_explore_tools(memory_reader, skills),
            *make_memory_write_tools(memory_writer),
            *make_file_tools(resource_dir),
        ],
    )
    orchestrator = Orchestrator(
        subagent,
        DbAgentRuns(db_file),
        skills,
        ZoneInfo(settings.timezone),
    )
    approvals = ApprovalRegistry()
    personas = PersonaService(
        settings.personas_dir,
        SettingsService(db_file),
        DbPersonaVersions(db_file),
        session_marker=daily_log.record_session_boundary,
    )
    try:
        snapshot = personas.current()
        logger.info("人设已启用：%s，版本：%s", snapshot.id, snapshot.version)
    except PersonaUnavailableError as exc:
        logger.warning("%s。对话将返回人设不可用，请在设置中切换人设", exc)
    except (sqlite3.Error, OSError) as exc:
        logger.error("人设版本记录失败：%s", exc)
        print(f"人设版本记录失败：{exc}", file=sys.stderr)
        return 1
    publisher = BusEventPublisher(event_bus)
    avatars = AvatarService(
        settings.data_dir / "avatars",
        SettingsService(db_file),
        seed_dir=AVATAR_SOURCE_DIR / "default",
    )
    interaction_service = InteractionService(settings_service)
    menu_service = MenuService(avatars, perception.scheduler)
    chat_service = ChatService(
        llm_service,
        daily_log,
        personas,
        templates,
        db_usage_sink(db_file),
        publisher,
        structured=structured_memory,
        timezone=ZoneInfo(settings.timezone),
        capabilities=merge_capabilities(capabilities, skills.summaries()),
        resources=default_resources(resource_dir),
        orchestrator=orchestrator,
        approvals=approvals,
        perception=perception,
        activity=chat_activity,
        interactions=interaction_service,
    )
    proactive = ProactivePipeline(
        chat_service,
        perception.scheduler,
        publisher,
        chat_active=chat_activity.is_active,
        status=publisher.status,
    )
    perception_runtime = PerceptionRuntime(perception, on_trigger=proactive.handle)
    app = create_app(
        settings.token.get_secret_value(),
        request_shutdown=shutdown.request,
        llm_service=llm_service,
        event_bus=event_bus,
        event_publisher=publisher,
        chat_service=chat_service,
        memories=memory_services,
        persona_service=personas,
        approvals=approvals,
        perception_service=perception,
        perception_runtime=perception_runtime,
        avatar_service=avatars,
        interaction_service=interaction_service,
        menu_service=menu_service,
    )
    config = uvicorn.Config(
        app,
        log_config=None,
        log_level=None,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = uvicorn.Server(config)
    shutdown.attach(server)
    if sys.stdin is not None:
        watch_stdin(sys.stdin, shutdown.request)

    server.run(sockets=[sock])
    logger.info("后端已退出，退出原因：%s", shutdown.reason or "信号")
    db_log.close()
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        settings = load_service_settings()
    except ServiceSettingsError as exc:
        for error in exc.errors:
            print(f"配置错误：{error}", file=sys.stderr)
        return 1

    try:
        log_file = setup_logging(settings.log_level, settings.data_dir / "logs")
    except OSError as exc:
        print(f"日志初始化失败：{exc}", file=sys.stderr)
        return 1
    logger.info("日志文件：%s，级别：%s", log_file, settings.log_level)

    llm_service = LlmService(
        ModelSettingsSource(),
        usage_sink=db_usage_sink(settings.data_dir / "pet.db"),
        active_store=SettingsService(settings.data_dir / "pet.db"),
    )
    try:
        llm_service.start()
    except ModelSettingsError as exc:
        for error in exc.errors:
            logger.error("LLM 配置错误：%s", error)
            print(f"LLM 配置错误：{error}", file=sys.stderr)
        return 1
    logger.info("LLM 配置已加载，版本：%s", llm_service.current().version)

    cards = scan_cards(settings.personas_dir)
    if not any(card.available for card in cards):
        if not cards:
            message = f"未找到人设卡片：{settings.personas_dir}"
            logger.error(message)
            print(message, file=sys.stderr)
        for card in cards:
            for error in card.errors:
                logger.error("人设配置错误（%s）：%s", card.id, error)
                print(f"人设配置错误（{card.id}）：{error}", file=sys.stderr)
        return 1
    logger.info("人设卡片：%s", "、".join(card.id for card in cards))

    try:
        templates = load_templates()
    except PromptError as exc:
        logger.error("提示词模板错误：%s", exc)
        print(f"提示词模板错误：{exc}", file=sys.stderr)
        return 1
    logger.info("提示词模板已加载，哈希：%s", templates.digest)

    try:
        capabilities = load_capabilities()
    except PromptError as exc:
        logger.error("能力列表错误：%s", exc)
        print(f"能力列表错误：{exc}", file=sys.stderr)
        return 1

    lock = SingleInstanceLock(settings.data_dir / "backend.lock")
    try:
        lock.acquire()
    except InstanceLockError as exc:
        logger.error("%s，本实例退出", exc)
        print(f"{exc}，本实例退出", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"单实例锁获取失败：{exc}", file=sys.stderr)
        return 1

    try:
        return serve(settings, llm_service, templates, capabilities)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
