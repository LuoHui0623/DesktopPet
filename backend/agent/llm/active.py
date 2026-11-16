"""`llm` 段的活跃模型选择：能力 → provider/model。

`llm.yaml` 的 provider 定义是部署级配置，不进快照；这里只存用户在设置页选中的
provider/model（`D12`）。选择是领域状态，落盘由设置协调器负责。
"""

from collections.abc import Mapping

from agent.spec.settings import SectionValues, SettingsSource
from config.settings import CAPABILITY_VARS, DefaultModel

SECTION = "llm"


class EmptySettingsSource:
    """无快照时的空段来源：顺序为「未配置 → 回落 .env 默认」。"""

    def section(self, name: str) -> SectionValues:
        return {}


def decode(values: SectionValues) -> dict[str, DefaultModel]:
    """段字段 → 能力到模型的映射；结构不符的条目直接丢弃。"""
    raw = values.get("active")
    if not isinstance(raw, dict):
        return {}
    selected: dict[str, DefaultModel] = {}
    for capability in CAPABILITY_VARS:
        item = raw.get(capability)
        if not isinstance(item, dict):
            continue
        provider = item.get("provider")
        model = item.get("model")
        if isinstance(provider, str) and isinstance(model, str) and provider and model:
            selected[capability] = DefaultModel(provider=provider, model=model)
    return selected


def encode(selected: Mapping[str, DefaultModel]) -> dict[str, object]:
    return {
        "active": {
            capability: {"provider": ref.provider, "model": ref.model}
            for capability, ref in selected.items()
        }
    }


class LlmActiveSettings:
    """活跃模型的领域状态：写入只改内存，读取以内存为准。"""

    def __init__(self, source: SettingsSource | None = None) -> None:
        self._source = source or EmptySettingsSource()
        self._state: dict[str, DefaultModel] | None = None

    def read(self) -> dict[str, DefaultModel]:
        if self._state is None:
            self._state = decode(self._source.section(SECTION))
        return dict(self._state)

    def write(self, selections: Mapping[str, DefaultModel]) -> None:
        merged = self.read()
        merged.update(selections)
        self._state = merged

    def snapshot(self) -> dict[str, object]:
        return encode(self.read())
