import threading
from types import TracebackType


class ChatActivity:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0

    def is_active(self) -> bool:
        with self._lock:
            return self._active > 0

    def __enter__(self) -> "ChatActivity":
        with self._lock:
            self._active += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        with self._lock:
            self._active -= 1
