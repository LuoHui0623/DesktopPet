import re
from collections.abc import Iterable

MAX_FRAME_EDGE = 512
MAX_FRAMES_PER_STATE = 24
MAX_FRAME_BYTES = 300 * 1024
MAX_PACK_BYTES = 15 * 1024 * 1024
MAX_FRAME_MS = 60_000
DEFAULT_AVATAR_ID = "default"
ACTIVE_AVATAR_PREFIX = "avatar.active."
FRAME_SUFFIXES = (".png", ".webp", ".gif")
STATIC_SUFFIX = ".png"
STATE_KEY = re.compile(r"^[a-z0-9_-]{1,32}$")


def active_key(persona_id: str) -> str:
    """每个启用人设分别记住一个形象包 id。"""
    return f"{ACTIVE_AVATAR_PREFIX}{persona_id}"


# 内置状态只是注册表的初始内容；新增状态通过 StateRegistry.register 登记。
PRESET_STATES = (
    "idle",
    "thinking",
    "speaking",
    "executing",
    "sleeping",
    "disconnected",
)


class StateRegistry:
    """有序状态注册表：manifest 校验与目录响应共用同一份词表。"""

    def __init__(self, initial: Iterable[str] = PRESET_STATES) -> None:
        self._states: list[str] = []
        for key in initial:
            self.register(key)

    @property
    def states(self) -> tuple[str, ...]:
        return tuple(self._states)

    def __contains__(self, key: object) -> bool:
        return key in self._states

    def register(self, key: str) -> tuple[str, ...]:
        if not STATE_KEY.match(key):
            raise ValueError(f"状态名 {key!r} 不合法")
        if key not in self._states:
            self._states.append(key)
        return self.states

    def unregister(self, key: str) -> tuple[str, ...]:
        if key in self._states:
            self._states.remove(key)
        return self.states


DEFAULT_STATE_REGISTRY = StateRegistry()
