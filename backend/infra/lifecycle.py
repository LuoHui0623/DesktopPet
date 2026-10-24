import logging
import threading
from collections.abc import Callable
from typing import IO, Protocol

logger = logging.getLogger("pet.lifecycle")

STDIN_SHUTDOWN_COMMAND = "shutdown"


class ExitableServer(Protocol):
    should_exit: bool


class ShutdownController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._servers: list[ExitableServer] = []
        self._reason: str | None = None

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def attach(self, server: ExitableServer) -> None:
        with self._lock:
            self._servers.append(server)
            if self._reason is not None:
                server.should_exit = True

    def request(self, reason: str) -> bool:
        with self._lock:
            if self._reason is not None:
                return False
            self._reason = reason
            for server in self._servers:
                server.should_exit = True
        logger.info("收到退出请求，来源：%s", reason)
        return True


def watch_stdin(stream: IO[str], request: Callable[[str], bool]) -> threading.Thread:
    def run() -> None:
        for line in stream:
            if line.strip() == STDIN_SHUTDOWN_COMMAND:
                request("stdin 指令")
                return
        request("stdin 已关闭")

    thread = threading.Thread(target=run, name="pet-stdin-watcher", daemon=True)
    thread.start()
    return thread
