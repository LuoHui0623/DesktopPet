"""运行期机制：周期性驱动与后台循环。

本包只放不承载业务规则的机制，当前仅有 `tick.py`（`PerceptionRuntime`，
按固定间隔驱动感知 tick 并把 `Trigger` 交给回调）。业务规则属于 `agent/`，
技术适配属于 `infra/`。
"""
