# 观测域设计：观测器与解析器

> 职责：定义观测器看什么、产出什么，解析器如何把观测变成“可进对话的 current”与“可触发管线的 Trigger”。
> 依赖：观测域内的常量与配置；解析器通过注入端口读 `chat_active` 与发送台账。不依赖 chat / llm / proactive 的实现。
> 不回答：Win32 采集细节（见 [PERCEPTION.md](PERCEPTION.md) §3）、话术内容（见 `docs/CHAT.md` / `docs/PROACTIVE.md`）、模型选择（见 `docs/LLM.md`）。
> 状态：已实现（2026-10-29）。契约已落地；观测器、解析器、调度仍集中在 `service/perception/`，包拆分未定。

## 1. 定位

观测域只回答两个问题，分成两段：

| 段 | 问题 | 产出 | 不做什么 |
|---|---|---|---|
| 观测器 observer | 现在发生了什么 | Snapshot、current 事件、时间事件 | 不判断该不该说话；不读勿扰/限额；不写台账；不碰 chat / llm |
| 解析器 parser | 这些事意味着什么 | 给 chat 的 context events；给管线的 Trigger | 不采集；不调模型；不生成话术；不推送 |

三段链：

```
Rust / Tauri 采样
  └─► 观测器（内存）
        ├─ Snapshot ─────────────────► API / 设置页
        ├─ current + 时间事件
        │     └─► 解析器（唯一读口）
        │           ├─ context events ─► chat（被动上下文）
        │           └─ Trigger{topic, reason} ─► proactive 管线
        └─（不落库）
```

五条硬约束：

1. chat 不直接读观测器；只通过解析器的 `take_context_events()` 拿 current。
2. 解析器是 current 的唯一消费者；被动对话读入后清空，触发时也清空。
3. 观测器与解析器都不 import chat / llm / proactive；解析器判断“对话进行中”只通过注入的 `chat_active` 端口。
4. 观测域不落库；重启后由采集端重新采样重建。
5. 话术、模型、气泡都不在观测域。

## 2. 概念定义

- **观测器**：接收采集端样本，维护 presence / busy / foreground / processes / idle 的内存状态，产出状态快照、current 事件与时间事件。它只描述事实，不决定是否开口。
- **解析器**：消费观测器事件，做事件→主题映射、话术门槛、开口闸门，产出 Trigger 或给 chat 的 context events。它是观测域对 chat / proactive 的唯一读口。
- **current**：已经发生、尚未被消费的事件批次。存在观测域内存（实现上可放观测器或解析器），解析器独占读取；被动对话读入后清空，触发时也清空。
- **时间事件**：由时钟 + 状态推导出的事实，例如进入深夜窗口、连续活跃达到 90 分钟、离开达到 10 分钟。它是事实，不是“该说话了”。
- **测量阈值**：定义一个事实何时成立、何时算一个事件。例如空闲 10 分钟 = away、前台进程连续 3 分钟 = in_use、启动稳定 30 秒。归观测器。
- **话术门槛**：定义一个已经成立的事实是否值得开口。例如离开 ≥10 分钟才打招呼、忙碌 ≥30 分钟才补一句、应用运行 ≥10 分钟才说收工。归解析器。
- **开口闸门**：是否允许现在开口的全局条件：勿扰、最小间隔、每日上限、主题上限、离开、忙碌、对话中、深夜只放 late_night。归解析器。
- **Trigger**：解析器交给管线的唯一指令，只有 `topic` 与 `reason`；不携带话术、不携带 instructions。

观测器内存只保存两类东西：**current 事件**、**时间事件**。不保存原始样本，不保存历史。

## 3. 依赖与落点

```
observer ← parser ← chat
                  ← proactive
proactive → chat
chat → llm
```

| 组件 | 目标落点 | 现状 |
|---|---|---|
| 观测器 | `module/perception/observer.py`（或保留 `service/perception/service.py`） | `service/perception/service.py` |
| 解析器 | `module/perception/parser.py` | `service/perception/parser.py`，内含主题映射与闸门 |
| 闸门配置 / 台账 | 解析器读配置与台账；管线写台账 | `service/perception/scheduler.py`，职责待拆 |
| 运行时 | 观测域单一时钟：`observer.tick → parser.consume → on_trigger` | `service/perception/runtime.py` |
| 管线 | `module/proactive/pipeline.py` | 已实现，由运行时投入工作线程执行 |

包迁移未定；本文只约定“谁依赖谁”，不把包名写死。

## 4. 对外契约

### 4.1 输入：感知样本

采集端（Tauri / Rust）变化时 `POST /api/perception`，请求体：

| 字段 | 类型 | 说明 |
|---|---|---|
| `kind` | string | `idle` / `foreground` / `fullscreen` / `locked` / `processes` / `process_candidate` |
| `value` | int / bool / string / string[] / null | 按 `kind` 校验 |
| `at` | ISO 8601（带时区） | 采集时刻 |

观测器负责：

- `kind` 白名单校验；非法返回 422。
- 同一 `kind`、同一 `value`、5 秒内重复上报去重。
- `perception.enabled=false` 时整体丢弃；`perception.foreground_enabled=false` 时只丢弃进程类。
- 前台进程 / 进程列表 / 候选进程只接受合法 exe 名；不在白名单的前台进程直接置空，不产生事件。
- 候选进程只留内存（最近 20 个），不落库；用户允许后进白名单，忽略后进忽略名单。

Win32 采集方式、系统外壳过滤、窗口化全屏判定见 [PERCEPTION.md](PERCEPTION.md) §3；本文不重复。

### 4.2 观测器输出

**Snapshot（只读状态）**

| 字段 | 含义 |
|---|---|
| `enabled` / `foreground_enabled` | 开关现状 |
| `presence` | `active` / `away` / `unknown` |
| `busy` | 全屏或锁屏；观测器负责 3 秒进入 / 30 秒退出防抖（现状在解析器） |
| `idle_seconds` | 全系统空闲秒数；未知为 null |
| `foreground` | 当前前台白名单 exe；关闭进程感知时为 null |
| `whitelist` / `muted` / `candidates` | 白名单、忽略名单、内存候选 |

Snapshot 供 `GET /api/perception/state`、设置页与解析器读取；它不进对话上下文。

**ObservedEvent（事实事件）**

```text
ObservedEvent {
  kind, at, exe?, minutes?, summary, in_context
}
```

九种 `kind`：

`presence_away`、`presence_return`、`sit_long`、`busy_enter`、`busy_exit`、`late_night`、`app_in_use`、`app_launched`、`app_quit`。

约束：

- 事件**不带 topic 字段**。事件→主题的映射归解析器。现状 `PerceptionEvent.topic` 是历史遗留，目标移除。
- 事件**不带话术**。`summary` 只用于日志与上下文的事实描述，不作为模型指令。
- `in_context` 只表示“允许进入被动对话上下文”，不表示“会触发主动”。是否触发由解析器决定。

**current 与时间事件**

- 观测器把事件写入观测域内存的 current 批次；解析器是唯一读口。
- 时间事件由观测器的周期 tick 产出：深夜窗口、连续活跃 / 离开 / 忙碌时长等。
- current 不缓存历史；进程内，随会话；触发或被动读入后清空。

### 4.3 解析器输出

**给 chat 的 context events**

```text
parser.take_context_events(limit) -> tuple[ObservedEvent, ...]
```

- 只返回 `in_context=true` 的事件，最多 5 条（`CONTEXT_EVENT_LIMIT`），按原始发生时间升序。
- `app_in_use` 只保留最近 1 条。
- 读取后清空 current。
- 被动 chat 在组装上下文时调用；主动轮不调用。

**给管线的 Trigger**

```text
Trigger { topic: str, reason: str }
```

- `topic` 是主动消息的唯一键，同时承担去重与限额；新增一种话术 = 新增一个主题键。
- `reason` 是事实摘要，供管线与 chat 了解“为什么触发”，不是话术。
- 解析器每次 tick 返回 0..n 个 Trigger；有 Trigger 时清空 current。
- 解析器不调用模型、不推送、不写台账。

### 4.4 消费顺序

每次 tick：

```
observer.tick(now)
  → parser.consume(now)
      ├─ 无 Trigger：current 保留，等被动 chat 读取
      └─ 有 Trigger：清空 current，交管线
```

被动对话：

```
用户发消息
  → chat 向 parser.take_context_events(limit)
  → current 清空
  → 组装上下文
```

主动轮：

```
parser 产出 Trigger
  → 管线拿 Trigger.reason
  → 调 chat（不再读 current）
```

`ingest` 也会更新状态并可能排队 Trigger，但统一在下一个 tick 交付（≤5 秒），不额外开一条推送路径。

这条顺序保证：同一条事件不会既进主动提示、又进被动上下文；也不会被被动对话先吃掉，导致主动轮看不到。

## 5. 事件与主题的归属

| 观测器事件 | 进上下文 | 解析器主题 | 话术门槛（解析器） |
|---|---|---|---|
| `presence_away` | 否 | — | — |
| `presence_return` | 是 | `greeting_return` | `away_minutes ≥ 10` |
| `sit_long` | 否 | `sit_long` | —（观测器 90 分钟测量） |
| `busy_enter` | 否 | — | — |
| `busy_exit` | 是 | `busy_released` | `busy_minutes ≥ 30` |
| `late_night` | 否 | `late_night` | 仅深夜窗口 |
| `app_in_use.<exe>` | 是（最近 1 条） | `app_in_use.<exe>` | 每进程每日 1 次 |
| `app_launched.<exe>` | 是 | `app_launched.<exe>` | 启动稳定 30 秒 |
| `app_quit.<exe>` | 是 | `app_quit.<exe>` | `run_minutes ≥ 10` |

`presence_away` / `busy_enter` 只作为状态事实与上下文抑制依据，不产生主题。

## 6. 阈值归属

| 规则 | 归属 | 理由 |
|---|---|---|
| 空闲 10 分钟 → away | 观测器 | 状态定义 |
| 全屏 / 锁屏 3 秒 → busy；退出 30 秒确认 | 观测器 | 状态防抖 |
| 前台进程连续 3 分钟 → `app_in_use` | 观测器 | 事件成立条件 |
| 白名单进程启动稳定 30 秒 | 观测器 | 事件成立条件 |
| 连续活跃 90 分钟 → `sit_long`；其后每 60 分钟一次 | 观测器 | 测量阈值 |
| 深夜 23:00–05:00 | 观测器 | 时间事实 |
| 离开 ≥10 分钟才打招呼 | 解析器 | 话术门槛 |
| 忙碌 ≥30 分钟才补一句 | 解析器 | 话术门槛 |
| 应用运行 ≥10 分钟才说收工 | 解析器 | 话术门槛 |
| 事件→主题键 | 解析器 | 话术契约 |
| 勿扰、最小间隔、每日上限、主题上限 | 解析器 | 开口闸门 |
| 深夜只放 `late_night` | 解析器 | 开口闸门 |
| `scheduler.sent` 台账 | 解析器读 / 管线写 | 跨重启去重 |

## 7. 开口闸门

解析器在产出 Trigger 前逐条检查；任一不满足即丢弃，并记录一次判定理由（同一 `topic + 抑制原因` 不重复刷日志）。

| 规则 | 默认值 | 可配 |
|---|---|---|
| 调度总开关 | `true` | 是 |
| 最小间隔 | 60 分钟 | 是 |
| 每日上限 | 20 次 | 是 |
| 同一主题每日上限 | 1 次（`greeting_return` / `sit_long` 各 3 次） | 否 |
| 勿扰时段 | 22:30–08:00，支持跨日 | 是 |
| 离开状态 | 不开口 | 否 |
| 忙碌（全屏 / 锁屏） | 不开口 | 否 |
| 对话进行中 | 不开口（注入 `chat_active` 端口） | 否 |
| 深夜 23:00–05:00 | 只允许 `late_night` | 否 |

台账：`app_setting` 的 `scheduler.sent`，形状 `{date, topics: {key: n}, total: n}`；解析器读，管线在发送成功后写；跨日归零。

已知限制：最小间隔依赖进程内 `last_sent_at`，重启后从空开始；台账只保当日计数，不保上次发送时刻。

## 8. 配置

| 键 | 归属 | 默认 | 说明 |
|---|---|---|---|
| `perception.enabled` | 观测器 | `true` | 总开关；关闭后采集端停机 |
| `perception.idle_away_minutes` | 观测器 | `10` | 离开测量阈值 |
| `perception.sit_minutes` | 观测器 | `90` | 久坐测量阈值 |
| `perception.foreground_enabled` | 观测器 | `true` | 只关进程相关 |
| `perception.process_whitelist` | 观测器 | `[]` | exe 名数组 |
| `perception.process_muted` | 观测器 | `[]` | exe 名数组 |
| `scheduler.enabled` | 解析器 | `true` | 主动总开关 |
| `scheduler.min_interval_minutes` | 解析器 | `60` | |
| `scheduler.max_per_day` | 解析器 | `20` | |
| `scheduler.dnd_start` / `scheduler.dnd_end` | 解析器 | `22:30` / `08:00` | 支持跨日 |
| `scheduler.sent` | 解析器读 / 管线写 | 空 | 当日台账 |

`perception.topics`（话术字典）不属于观测域：解析器只产出 topic 键，具体话术由 chat / 管线决定。若保留该配置，应移到 proactive 配置；此项待定。

## 9. 非目标

1. **不落库**：不写 `daily_log`、不建感知表、不写结构化记忆、不扩 `EVENT_TYPES`。
2. **不提供历史查询**；设置页“清空感知”只清内存。
3. **不生成话术、不调模型、不推 WS、不画气泡**。
4. **不做多消费者**：解析器是 current 的唯一读口。
5. **不把 Win32 采集搬到后端**；采集在 Tauri / Rust。

## 10. 与事件分层的关系

| 层 | 内容 | 归属 | 落库 |
|---|---|---|---|
| L1 实时状态 | `Snapshot` | 观测器 | 否 |
| L2 感知流水 | current 事实事件 | 观测域 | 否 |
| L3 有价值事件 | 完成、审批拒绝、派发失败等 | chat / memory | `daily_log` |

感知九种事件全部属于 L2，不进 L3。

## 11. 待定与迁移

| 项 | 现状 | 目标 |
|---|---|---|
| 包名 | `service/perception` | 是否迁 `module/perception` 未定 |
| 解析器职责 | 同时做事件、主题、闸门 | 保留“解析器集中判定”，但从观测器类中拆出 |
| 调度 | 配置 + 台账 + 闸门同包 | 闸门归解析器；台账写归管线 |
| `PerceptionEvent.topic` | 事件自带 topic | 移除；topic 归解析器 |
| `perception.topics` | 话术字典 | 移到 chat / proactive 或删除；待定 |
| chat 的注入对象 | 注入 `PerceptionService`，由其转调解析器 | 注入解析器的读接口，不经过观测器 |
| 主动轮读 current | 触发时清空 | 明确主动轮只用 `Trigger.reason` |

## 12. 已定口径与关联文档

- 解析器集中判定，只输出 `Trigger{topic, reason}`。
- 感知事件不落库，不写 `daily_log`，不扩 `EVENT_TYPES`。
- ChatService 提供完整 chat 能力，不做主动编排；主动轮只用 `Trigger.reason`，不复读 current。
- 上下文来自当日会话快照；current 与历史用分隔串区分。
- [PERCEPTION.md](PERCEPTION.md)：采集方式、状态模型、九种事件的原始判断条件。
