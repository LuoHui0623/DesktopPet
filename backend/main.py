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
    load_service_settings,
)
from infra.events.bus import EventBus
from infra.files.json_store import JsonDirectory
from infra.lifecycle import ShutdownController, watch_stdin
from infra.llm.usage import db_usage_sink
from infra.lock import InstanceLockError, SingleInstanceLock
from infra.logging import attach_db_logging, setup_logging
from infra.store.agent_runs import DbAgentRuns
from infra.store.app_settings import DbAppSettings
from infra.store.core_memory import DbCoreMemory
from infra.store.daily_log import DbDailyLog
from infra.store.db import connect
from infra.store.migrate import MigrationError, migrate
from infra.store.persona_versions import DbPersonaVersions
from service.agent.approval import ApprovalRegistry
from service.agent.orchestrator import Orchestrator
from service.agent.prompt import (
    CapabilityDescription,
    PromptError,
    PromptTemplates,
    load_capabilities,
    load_templates,
    merge_capabilities,
)
from service.agent.skills import SkillRegistry, memory_write_skill
from service.agent.subagent import SubAgent
from service.agent.tools.memory import make_memory_tools, make_memory_write_tools
from service.chat.chat_service import ChatService
from service.llm import LlmService
from service.memory.core_memory import CoreMemoryService
from service.memory.daily_log_service import DailyLogService
from service.memory.index import MemoryIndexService
from service.memory.memory_service import MemoryReader, MemoryWriter, StructuredMemoryService
from service.memory.services import MemoryServices
from service.persona.persona_service import PersonaService, PersonaUnavailableError

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
    memory_dir = settings.data_dir / "memory"
    structured_dir = memory_dir / "structured"
    index_file = memory_dir / "index.json"
    daily_log = DailyLogService(
        DbDailyLog(db_file),
        DbAppSettings(db_file),
        ZoneInfo(settings.timezone),
    )
    structured_memory = StructuredMemoryService(
        JsonDirectory(structured_dir),
        DbAppSettings(db_file),
        ZoneInfo(settings.timezone),
    )
    memory_index = MemoryIndexService(structured_memory, daily_log.dates, index_file)
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
    core_memory = CoreMemoryService(DbCoreMemory(db_file), structured_memory)
    memory_writer = MemoryWriter(structured_memory, daily_log, ZoneInfo(settings.timezone))
    memory_reader = MemoryReader(index_file, structured_memory, daily_log)
    memory_services = MemoryServices(
        structured=structured_memory,
        writer=memory_writer,
        daily=daily_log,
        core=core_memory,
    )
    skills = SkillRegistry()
    skills.register(memory_write_skill())
    subagent = SubAgent(
        llm_service,
        [*make_memory_tools(memory_reader), *make_memory_write_tools(memory_writer)],
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
        DbAppSettings(db_file),
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
    chat_service = ChatService(
        llm_service,
        daily_log,
        personas,
        templates,
        db_usage_sink(db_file),
        BusEventPublisher(event_bus),
        core=core_memory,
        structured=structured_memory,
        timezone=ZoneInfo(settings.timezone),
        capabilities=merge_capabilities(capabilities, skills.summaries()),
        orchestrator=orchestrator,
        approvals=approvals,
    )
    app = create_app(
        settings.token.get_secret_value(),
        request_shutdown=shutdown.request,
        llm_service=llm_service,
        event_bus=event_bus,
        chat_service=chat_service,
        memories=memory_services,
        persona_service=personas,
        approvals=approvals,
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
        active_store=DbAppSettings(settings.data_dir / "pet.db"),
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
