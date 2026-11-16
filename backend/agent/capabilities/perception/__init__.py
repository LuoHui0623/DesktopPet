"""输入域：观测采集归一、九种事件、开口闸门与每日台账。

四段职责：

- `collector`：外部采样归一为运行态，产出快照与候选进程（`PerceptionService`）。
- `events`：值对象（事件、运行态、快照、触发）与事件成立判定（`PerceptionEventParser`）。
- `gate`：开口闸门的配置、勿扰时段与每日限额台账（`SchedulerRuntime` 等）。
- `interaction`：互动上报与上下文注入源。

运行期 tick 循环不在本包内，位于 `service/tick.py`（`PerceptionRuntime`）。
"""
