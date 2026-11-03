# 动画资源、时间轴与掩码

> 职责：定义统一动画资源、GIF 与 PNG/WebP 序列的解码、帧时长、资源 id、帧掩码、锚点与绘制矩形。
> 依赖：`service/avatar.service.ts`、`modules/pet/animation-resource.ts`、`avatar-decode.ts`、`frame-mask.ts`、`avatar-layout.ts`；浏览器 `ImageDecoder`。
> 不回答：包 manifest 校验、状态选择与回退、窗口穿透与指针事件。
> 状态：已实现。

## 1. 统一资源模型

GIF、PNG/WebP 序列与单帧静态图都解析为同一个 `AnimationResource`：

| 字段 | 含义 |
|---|---|
| `packId` | 所属形象包 |
| `source` | 资源来源；GIF 与单帧为主文件路径，序列为文件列表的标识 |
| `kind` | `gif` 或 `sequence` |
| `canvas` | 逻辑画布，等于 manifest 的 `frame` |
| `frames` | 帧列表 |

每帧包含：

| 字段 | 含义 |
|---|---|
| `id` | 资源 id：`packId/source#index` |
| `source` | 该帧的源文件；序列中每帧各自不同 |
| `index` | 在该动画中的序号 |
| `image` | 已解码的位图；不可用时为 `null` |
| `width` / `height` | 位图实际尺寸 |
| `durationMs` | 该帧显示时长，已归一化 |
| `mask` | 该帧的命中掩码；整帧透明或无法读取时为 `null` |

静态主形象是单帧 `sequence` 资源，不注册到任何状态。

## 2. 解码适配器

解码器是可注入接口：

```text
decode(source, blob) → DecodedAssetFrame[]
```

- PNG / WebP：`createImageBitmap` 得到单帧，时长 0，由 manifest 的 `frame_ms` 覆盖。
- GIF：使用 WebView 的 `ImageDecoder` 逐帧解码；每帧转成独立位图，读取文件自带的帧延迟。
- 不在浏览器侧做 disposal 合成；GIF 必须是整帧图，局部帧与依赖前帧叠加的资源不在契约内。
- `ImageDecoder` 不可用或无法逐帧输出时只替换适配器，不影响时间轴、掩码、渲染与穿透判定。
- 解码出的位图在切换包或释放形象时统一 `close()`；同一文件被多个状态引用只解码一次。

## 3. 帧时长与时间轴

- GIF 用文件自带延迟；PNG/WebP 序列用 manifest 的 `frame_ms`。
- 延迟 0 或非有限值按 100ms；低于 20ms 按 20ms。
- 播放按累计时长循环：

```text
总时长 = Σ 每帧 durationMs
当前帧 = 首次满足“累计时长 > elapsed % 总时长”的帧
```

- 状态切换时从新动画首帧重新计时。
- 空动画不产生帧，播放器返回 `null`，由渲染层显示静态主形象或占位。

## 4. 资源 id 与缓存

- 资源 id 为 `packId + "/" + 源路径 + "#" + 帧序号`。
- 掩码与位图按资源 id 归属，与状态名、动画节奏和 React 组件解耦。
- 同一源文件在多个状态被引用时，文件只下载与解码一次；动画对象各自持有帧列表。
- 换帧只切换当前帧引用，不重复解析图像、不重建掩码。

## 5. 帧掩码

- 显示直接使用图像自身 alpha；命中与穿透使用独立的 `HIT_ALPHA_THRESHOLD = 32`，两者不共用常量。
- 掩码是 1bpp 位图：每行 `ceil(width / 8)` 字节，位序从低位到高位，按行优先。
- 同一次扫描同时得到不透明像素包围盒；整帧透明时掩码为 `null`，该资源退化为整块可交互。
- 掩码带宽度、高度、行字节、阈值与哈希；哈希用于身份标识与调试，不参与缓存键。
- 掩码在加载期一次性生成；播放期只做位查询，不做像素扫描。
- 编码格式为 16 字节头（宽、高、行字节、阈值）加位图；运行时以对象缓存为主，编码用于稳定传递与测试。

## 6. 锚点与绘制矩形

- 锚点取资源自身矩形中心（自然宽高），不取 alpha 包围盒中心，也不取帧间有色并集。
- 同一包内所有帧尺寸等于 `frame`，因此天然共用同一锚点；前端仍按资源记录矩形，不把该约束当隐含前提。
- 渲染按 `contain` 把逻辑画布放进槽位：

```text
scale = min(slotWidth / canvasWidth, slotHeight / canvasHeight)
drawWidth = canvasWidth × scale
drawHeight = canvasHeight × scale
drawLeft = (slotWidth - drawWidth) / 2
drawTop = slotHeight - drawHeight
```

- 水平对齐槽位中心，底边贴齐槽位底部。
- 绘制、命中换算与穿透判定共用同一个绘制矩形，不使用元素外框代替。
- 反应动画的 `transform-origin` 取绘制矩形中心。

## 7. 加载编排

1. 读取目录并确定候选包（当前包优先，默认包兜底）。
2. 先解码 `static` 主形象，生成单帧资源并发布预览。
3. 再处理 `preset_states` 允许的状态，同一状态的多帧并行解码，同一源文件命中解码缓存。
4. 组装动画表与状态表；静态主形象不注册到任何状态。
5. 主形象加载失败的包跳过，换下一个候选；全部失败返回 `null`，由界面显示占位。
6. 换包或卸载时释放全部位图，避免共享位图重复关闭。

## 8. 约束与非目标

- 不引入 Pillow、不做后端像素解码或 GIF 转码；动画解码只在 WebView 加载期完成。
- GIF89a 只有 1-bit 透明与 256 色，软边会退化为硬边；这是资源侧取舍。
- 不做 disposal 合成；包内 GIF 必须是整帧图。
- 不保证 `ImageDecoder` 在所有 WebView 版本可用；适配器可替换。
- 不在动画层做命中判定或窗口开关；掩码与绘制矩形只提供数据。

## 9. 已定口径

- GIF 与 PNG/WebP 序列产出同一 `AnimationResource`；静态资源即单帧序列。
- 帧时长来源固定：GIF 用文件延迟，序列用 `frame_ms`；0 延迟按 100ms，低于 20ms 按 20ms。
- 掩码与资源 id 绑定，加载期一次生成；播放期不重复解析。
- 锚点取资源矩形中心，布局水平居中、底边贴底，绘制、命中与穿透共用绘制矩形。
- 包与 manifest 见 [PACK.md](PACK.md)；状态选择见 [STATE.md](STATE.md)；窗口使用方式见 [WINDOW.md](WINDOW.md)。
