import socket
import sys

import uvicorn

from api.app import create_app
from config.settings import ServiceSettingsError, load_service_settings


def bind_socket(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


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
        sock = bind_socket(settings.host, settings.port)
    except OSError as exc:
        print(f"无法绑定 {settings.host}:{settings.port}：{exc}", file=sys.stderr)
        return 1

    print(f"PET_PORT={sock.getsockname()[1]}", flush=True)

    config = uvicorn.Config(create_app(), log_level=settings.log_level.lower())
    uvicorn.Server(config).run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
