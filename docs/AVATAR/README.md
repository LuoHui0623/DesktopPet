# 形象域

> 职责：定义形象域的边界、概念与阅读路径，汇总形象包、状态、动画资源与悬浮窗交互的权威入口。
> 依赖：`module/avatar`、`api/routers/avatars`、前端 `service/avatar.service.ts` 与 `modules/pet/`；状态事件来自聊天与连接层。
> 不回答：聊天何时产生状态事件、设置页表单布局、原生菜单与跨平台穿透。
> 状态：已实现（形象组与人设挂载、形象包、状态、动画资源、悬浮窗命中与穿透）。

## 定位

形象域只负责“当前该显示什么”，不决定“什么时候该显示”。

| 做什么 | 不做什么 |
|---|---|
| 扫描形象包、校验资源、维护当前启用项 | 产生聊天状态事件、判断主动开口 |
| 维护状态注册表与行为解析器，把行为解析为状态键 | 编排聊天状态机、设置状态优先级 |
| 把动画挂载到状态，产出统一的 `AnimationResource` | 按状态生成话术、操作聊天记录 |
| 提供加载期生成的帧掩码、播放时间轴与回退链 | 决定设置页表单结构与交互 |
| 定义悬浮窗的可交互范围与穿透开关契约 | 决定聊天、互动与主动管线的业务时机 |

主链路固定为：

```text
行为解析 → 状态 → 动画
```

解析器只回答“对应哪个状态”；状态层只回答“这个状态挂的是哪个动画”；动画资源只回答“怎么画、画到哪一帧、这一帧哪里可交互”。

## 主题与阅读路径

| 文档 | 回答什么 | 不回答什么 |
|---|---|---|
| [PACK.md](PACK.md) | 形象包目录、`manifest.json`、静态主形象、资源约束、目录接口、包级失败 | 状态选择、逐帧解码、窗口穿透 |
| [STATE.md](STATE.md) | 状态注册表、行为解析、状态表、回退链、状态扩展 | 事件产生时机、动画播放、命中判定 |
| [ANIMATION.md](ANIMATION.md) | 统一动画资源、GIF 与序列解码、时间轴、资源 id、帧掩码、锚点与绘制矩形 | 包校验、状态选择、窗口交互 |
| [WINDOW.md](WINDOW.md) | 悬浮窗渲染、可交互范围、坐标换算、按压锁定、光标轮询与穿透开关 | 包与状态解析、动画解码、聊天与互动业务 |
| [GROUP.md](GROUP.md) | 形象组、人设 id 归属、组内包选择、切换人设与缺组降级 | 人设内容、状态注册、动画解码、窗口命中 |

建议阅读顺序：

- 改形象包或资源校验：`PACK.md` → `ANIMATION.md`。
- 新增状态或行为：`STATE.md` → `WINDOW.md`（确认命中与播放不受影响）。
- 改悬浮窗命中、拖动或穿透：`WINDOW.md` → `ANIMATION.md` 的绘制矩形与掩码。
- 改形象与人设的归属：`GROUP.md` → `PACK.md`（包契约）→ `STATE.md`（切换后的状态表）。
- 查整体分层与代码落点：先读本页的「组件地图」，再读 [Arch.md](../../Arch.md) 的「形象域」。

## 概念定义

- **形象组（AvatarGroup）**：一个人设 id 对应的形象目录，内含一个或多个形象包；组目录名就是人设 id。归属与选择规则见 [GROUP.md](GROUP.md)。
- **形象包（AvatarPack）**：形象组下的一个包，含 `manifest.json`、一张静默主形象与若干动画资源。包是资源校验与目录下发的单位。
- **静默主形象（static）**：包级 PNG 主形象图，尺寸等于 manifest 的 `frame`；不属于任何状态，加载期先显示，并作为所有动态资源的最终兜底。
- **状态键（state key）**：形象资源的逻辑标签，例如 `idle`、`thinking`。包只能使用后端已注册的状态键。
- **状态注册表**：后端维护的有序状态集合，六个内置状态是初始内容，可登记新状态；清单随形象目录下发。
- **行为解析器（resolver）**：把行为事件映射为状态键的注册函数；只回答状态，不选帧、不碰掩码。
- **动画资源（AnimationResource）**：一个状态对应的完整动画，统一表示 GIF、PNG/WebP 序列与单帧静态图。字段为逻辑画布、帧列表、每帧时长与每帧掩码。
- **资源 id**：`packId + 源路径 + 帧序号`，用于位图、掩码与换帧去重；同一文件被多个状态引用时只解码一次。
- **帧掩码**：按命中阈值生成的 1bpp 位图与包围盒，加载期一次生成，播放期只查表；显示仍使用图像自身 alpha。
- **回退链**：状态动画 → manifest `fallback` 动画 → 静默主形象 → 内联占位。

## 组件地图

```text
行为事件（聊天状态 / 连接状态 / 后续行为）
  └─► 行为解析器 ─► 状态键
                      └─► 状态层（state → AnimationResource）
                            └─► 播放 / 命中与穿透判定

module/avatar ─► GET /api/avatars {persona, active, preset_states, packs} ─► service/avatar.service.ts
```

| 组件 | 落点 | 说明 |
|---|---|---|
| 形象组、包扫描、manifest 校验、当前启用项 | `module/avatar/avatar_service.py` | 按当前人设扫描组内包，校验 manifest、GIF 元数据、静态主形象与帧尺寸，维护按人设记忆的启用项 |
| 状态注册表、GIF 与尺寸常量 | `module/avatar/constants.py` | 六态注册表，可登记新状态；`avatar.active.<persona_id>` 记忆键 |
| 域内契约对象 | `module/avatar/contracts.py` | 目录携带 `persona`、可空 `active`、`preset_states` 与包 `static` |
| 目录与资源接口 | `api/routers/avatars.py`、`api/schemas/avatars.py` | 下发目录；按 `<persona_id>/<pack_id>/<path>` 提供带令牌的资源响应 |
| 资源下载与释放 | `frontend/src/service/avatar.service.ts` | 目录读取、带令牌下载、加载期解码与释放 |
| 动画解析、状态层、行为解析器 | `frontend/src/modules/pet/animation-resource.ts`、`avatar-state.ts`、`avatar-decode.ts` | 纯数据与适配器，不与 React 组件耦合 |
| 掩码、绘制矩形与命中 | `frontend/src/modules/pet/frame-mask.ts`、`avatar-layout.ts`、`avatar-hit.ts` | 加载期生成掩码；绘制、命中与穿透共用绘制矩形 |
| 播放与交互接线 | `frontend/src/modules/pet/PetView.tsx`、`PetWindow.tsx`、`use-avatar-frame.ts`、`pass-through.ts`、`pet-hit-state.ts` | canvas 逐帧播放、光标轮询切换穿透、按压锁定 |

## 跨域边界

- 聊天状态事件由 [CHAT.md](../CHAT.md) 负责；主动轮与主动气泡由 [PROACTIVE.md](../PROACTIVE.md) 负责。
- 互动的上报、节流与上下文注入由 [Arch.md](../../Arch.md) 的「基础互动」负责；右键菜单读模型见「右键菜单」。
- 窗口分层、目录与接口落点见 [Arch.md](../../Arch.md) 的「形象域」；本目录只写形象域内部契约。
- 设置页“形象”tab 只消费目录接口并提交当前启用项，不参与状态解析与命中判定。

## 文档维护规则

1. 一个规则只在一份主题文档成文，其他文档只引用不复制。
2. 包、状态、动画、窗口交互的边界变化时，同时更新本页的「主题与阅读路径」的阅读路径与 [docs/README.md](../README.md) 的结论索引。
3. 形象域内部实现落点变化时更新本页的「组件地图」与 `Arch.md` 的「形象域」，不在主题文档里重复目录树。
4. 已确认但尚未实现的设计单独成文并标注“草案”，不得在已实现主题里写成现状；实现完成后同步状态与迁移说明。
