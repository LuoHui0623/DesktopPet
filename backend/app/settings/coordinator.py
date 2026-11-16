"""设置写入的唯一持久化入口（`D12`、`R8`）。

写入顺序固定：校验 `If-Match` 版本 → 调域方法改领域状态 → 拉该域 `snapshot()`
投射进内存文档 → 原子落盘。任何域内部都不直接写文件，绕过本协调器的写入会导致
快照陈旧。
"""

import threading
from collections.abc import Callable, Mapping

from app.settings.document import SettingsDocument
from app.settings.sections import is_section


class SettingsVersionConflict(Exception):
    """`If-Match` 版本与当前快照不一致。"""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"设置版本已变化：请求 {expected}，当前 {actual}")


class SettingsCoordinator:
    def __init__(
        self,
        document: SettingsDocument,
        sources: Mapping[str, Callable[[], Mapping[str, object]]],
    ) -> None:
        self._document = document
        self._sources = dict(sources)
        self._lock = threading.RLock()

    @property
    def version(self) -> int:
        return self._document.version

    def section(self, name: str) -> Mapping[str, object]:
        return self._document.section(name)

    def save(
        self,
        name: str,
        apply: Callable[[], object] | None = None,
        *,
        version: int,
    ) -> bool:
        """改领域状态并投射落盘；返回快照是否发生变化。"""
        if not is_section(name):
            raise KeyError(name)
        with self._lock:
            self._check(version)
            if apply is not None:
                apply()
            return self._project(name)

    def project(self, name: str) -> bool:
        """把领域状态投射到快照；副作用写入（进程名单、形象自愈）后调用。"""
        if not is_section(name):
            raise KeyError(name)
        with self._lock:
            return self._project(name)

    def _project(self, name: str) -> bool:
        source = self._sources.get(name)
        if source is None:
            raise KeyError(f"未注册的设置段：{name}")
        if not self._document.replace(name, source()):
            return False
        self._document.persist()
        return True

    def _check(self, version: int) -> None:
        current = self._document.version
        if version != current:
            raise SettingsVersionConflict(version, current)
