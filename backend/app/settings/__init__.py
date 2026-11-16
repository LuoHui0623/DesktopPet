"""设置入口：配置 schema、写入协调与持久化（`D11`、`D12`）。

`document.py` 拥有 `data/settings.json`（载入、校验、合并、持久化、版本号）；
`sections.py` 聚合六段的默认值与段级归一化；`coordinator.py` 是唯一写入口，
顺序固定为「改领域状态 → 拉 snapshot → 投射 → 落盘」。

`agent` 侧只经 `agent/spec/settings.py` 的 `SettingsSource` 读自己那一段，
不写文件，也不依赖本包。
"""
