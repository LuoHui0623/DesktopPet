import logging
import socket
import sys
from pathlib import Path

import uvicorn

from api.app import create_app
from config.settings import ServiceSettings, ServiceSettingsError, load_service_settings
from infra.lifecycle import ShutdownController, watch_stdin
from infra.lock import InstanceLockError, SingleInstanceLock
from infra.logging import attach_db_logging, setup_logging
from infra.store.db import connect
from infra.store.migrate import MigrationError, migrate

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
    except OSError:
        sock.close()
        raise
    return sock


def serve(settings: ServiceSettings) -> int:
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
    app = create_app(settings.token.get_secret_value(), request_shutdown=shutdown.request)
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
        return serve(settings)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
