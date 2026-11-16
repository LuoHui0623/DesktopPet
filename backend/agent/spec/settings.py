"""应用配置的只读窄契约：按段名取快照，不感知持久化形式。

`app/settings/` 拥有 `data/settings.json` 快照；`agent` 侧只读取自己那一段，
不写文件、不调持久化（`D12`）。本模块只放 Protocol 与类型别名，不引入实现。
"""

from collections.abc import Mapping
from typing import Protocol

# 段内字段名 → JSON 原生类型值；缺字段与类型不符由各段解码器回落默认值。
SectionValues = Mapping[str, object]


class SettingsSource(Protocol):
    """按段名返回配置快照；未知段返回空映射而不是抛错。"""

    def section(self, name: str) -> SectionValues: ...
