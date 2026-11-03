# 形象包与资源契约

> 职责：定义形象包目录、`manifest.json` 字段、静默主形象、资源约束、目录接口与包级失败。
> 依赖：`module/avatar/avatar_service.py`、`constants.py`、`contracts.py`、`api/routers/avatars.py`、`api/schemas/avatars.py`。
> 不回答：状态选择与行为解析、GIF 逐帧解码、掩码生成、窗口命中与穿透。
> 状态：已实现。

## 1. 包是什么

形象包是数据目录下的一个资源单位：

- 一个包对应一个目录，目录名就是包 id。
- 包声明自己的逻辑画布、主形象与状态动画。
- 包只能挂载后端已注册的状态键；未注册状态不会进入目录响应。
- 包是目录接口与设置页选择的最小单位。

## 2. 目录结构

包位于当前人设的形象组内，组目录名就是人设 id：

```text
data/avatars/<persona_id>/<pack_id>/
├─ manifest.json
├─ static.png
└─ <各状态资源，GIF 或 PNG/WebP 序列>
```

- `pack_id` 只允许字母、数字、下划线与连字符，长度 1–64。
- 人设归属只由组目录决定；组规则见 [GROUP.md](GROUP.md)。
- 资源路径必须是包内相对路径，使用 `/`，不允许反斜杠、绝对路径或 `..` 越界。
- 帧资源支持 `.png`、`.webp`、`.gif`；静默主形象只支持 `.png`。

## 3. manifest 契约

```json
{
  "id": "default",
  "name": "默认形象",
  "frame": { "width": 240, "height": 240 },
  "fallback": "idle",
  "static": "static.png",
  "states": {
    "idle": { "frames": ["idle_0.png"], "frame_ms": 160 }
  }
}
```

| 字段 | 约束 |
|---|---|
| `id` | 必填，必须等于包目录名 |
| `name` | 必填，1–64 字符 |
| `frame` | 必填，逻辑画布宽高；所有帧与主形象尺寸必须与之一致，边长不超过 512 |
| `fallback` | 必填，必须指向已注册且在已注册 `states` 中定义的状态 |
| `static` | 必填，包内 PNG 主形象路径；尺寸必须等于 `frame` |
| `states` | 必填且非空；键必须来自后端状态注册表 |

每个状态一个动画：

- `frames` 必填，1–24 项；GIF 必须单独一条，不能与其它帧文件混在同一状态。
- `frame_ms` 对 PNG / WebP 序列必须大于 0；GIF 状态可缺省或为 0，播放延迟以文件为准。
- 未注册状态名在加载时告警后忽略，不导致整个包被拒绝。
- `fallback` 在过滤未注册状态之后校验；指向未注册或未定义状态时拒绝该包。

资源上限：

| 限制 | 值 |
|---|---|
| 单帧 / 主形象文件 | ≤ 300KB |
| 形象包总大小 | ≤ 15MB |
| 每状态帧数 | 1–24（GIF 按子帧计数） |
| 帧边长 | ≤ 512 |
| 单帧延迟上限 | 60s |

## 4. 静默主形象

- 每个包必须提供 `static`，且只接受 PNG。
- 主形象不注册到任何状态，也不是某段动画的首帧替代物。
- 加载时先解码并显示主形象，状态动画就绪后替换。
- 主形象缺失、非 PNG、尺寸不等于 `frame`、路径越界或超过单文件上限时，拒绝整个包。
- `static` 与某状态帧指向同一文件时只计一次字节，不影响该状态的动画注册。

## 5. 目录接口

`GET /api/avatars` 返回当前人设、启用项、状态注册表与组内包列表：

```json
{
  "persona": "芙芙",
  "active": "default",
  "preset_states": ["idle", "thinking", "speaking", "executing", "sleeping", "disconnected"],
  "packs": [
    {
      "id": "default",
      "name": "默认形象",
      "active": true,
      "frame": { "width": 240, "height": 240 },
      "fallback": "idle",
      "static": "static.png",
      "states": { "idle": { "frames": ["idle_0.png"], "frame_ms": 160 } }
    }
  ]
}
```

- `persona` 是当前人设 id；`packs` 只包含当前人设组内的包。
- `active` 是当前包 id；组为空或没有可用包时为 `null`。
- `preset_states` 是后端注册表顺序，前端按它建立状态映射，不写死状态数量。
- 包列表中的 `states` 只含已注册状态；未注册状态不出现。
- 帧图与主形象走 `GET /api/avatars/<persona_id>/<pack_id>/<path>`，需令牌；人设段必须与当前人设一致，禁止越界路径。
- 不再提供不带人设段的资源路径；`.gif` 的 MIME 由文件响应按扩展名处理。

## 6. 当前启用项与配置

| 配置 | 默认值 | 说明 |
|---|---|---|
| 当前启用包 | 组内 `default`；没有时取第一个包 | `app_setting` 的 `avatar.active.<persona_id>`，按人设分别记忆 |
| 默认包 id | `default` | 组内优先选择的包目录名 |
| 单帧上限 | 300KB | R-02 |
| 整包上限 | 15MB | R-02 |
| 帧边长 | ≤ 512 | R-02 |
| 每状态帧数 | 1–24 | GIF 按子帧计数 |

启用项由 `PUT /api/avatars/active` 写入当前人设的记忆键；目标包不属于当前人设组或不存在时返回 404，不改变当前启用项。

## 7. 包级失败

| 情况 | 行为 |
|---|---|
| 包目录被删除或校验不通过 | 不出现在目录；当前启用项回退组默认包；组内没有可用包时 `active` 为 `null` |
| 静态主形象缺失或非法 | 拒绝该包，不进入目录 |
| 未注册状态名 | 告警后忽略，不进入状态层 |
| `fallback` 指向未注册或未定义状态 | 拒绝该包 |
| 帧尺寸不等于 `frame` | 拒绝该包 |
| GIF 块结构损坏、帧数超限或逻辑屏幕尺寸不符 | 拒绝该包 |
| GIF 含局部帧或依赖 disposal 合成 | 不拒绝；逐帧输出并告警，资源应改为整帧 GIF |
| 包总大小超限 | 拒绝该包 |

单帧 `frame_ms` 非法、GIF 延迟异常等播放侧问题的降级见 [ANIMATION.md](ANIMATION.md)；缺失状态的补位见 [STATE.md](STATE.md)。

## 8. 非目标

- 不引入 Pillow、不做后端像素解码或 GIF 转码；后端只读取 GIF 头与块结构。
- 不开放包级自定义状态；新增状态必须经后端注册表。
- 不接受包内绝对路径、`..` 越界或跨包引用。
- 不在包契约里定义形象组与人设挂载；该层规则单独成文。
