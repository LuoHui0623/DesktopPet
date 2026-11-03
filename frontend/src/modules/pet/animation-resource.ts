import type { FrameMask } from "./frame-mask";

export interface ResourceCanvas {
  width: number;
  height: number;
}

export type AnimationKind = "sequence" | "gif";

export interface RawAnimationFrame {
  image: CanvasImageSource | null;
  durationMs: number;
  mask: FrameMask | null;
  /** 序列里每帧来自不同文件时单独给出；GIF 与单帧资源可省略。 */
  source?: string;
}

export interface AnimationFrame extends RawAnimationFrame {
  /** 资源 id：packId + "/" + 源路径 + "#" + 帧序号。 */
  id: string;
  source: string;
  index: number;
  durationMs: number;
}

export interface AnimationResource {
  packId: string;
  source: string;
  kind: AnimationKind;
  canvas: ResourceCanvas;
  frames: readonly AnimationFrame[];
}

export const DEFAULT_FRAME_DELAY_MS = 100;
export const MIN_FRAME_DELAY_MS = 20;

/** GIF 延迟 0 按 100ms，低于 20ms 按 20ms；PNG 序列的 frame_ms 已由后端保证大于 0。 */
export function normalizeFrameDelay(durationMs: number): number {
  if (!Number.isFinite(durationMs) || durationMs <= 0) return DEFAULT_FRAME_DELAY_MS;
  return durationMs < MIN_FRAME_DELAY_MS ? MIN_FRAME_DELAY_MS : durationMs;
}

export function resourceFrameId(packId: string, source: string, index: number): string {
  return `${packId}/${source}#${index}`;
}

function build(
  kind: AnimationKind,
  packId: string,
  source: string,
  canvas: ResourceCanvas,
  frames: readonly RawAnimationFrame[],
): AnimationResource {
  return {
    packId,
    source,
    kind,
    canvas,
    frames: frames.map((frame, index) => {
      const frameSource = frame.source ?? source;
      return {
        ...frame,
        id: resourceFrameId(packId, frameSource, index),
        source: frameSource,
        index,
        durationMs: normalizeFrameDelay(frame.durationMs),
      };
    }),
  };
}

/** PNG / WebP 序列：每个文件一帧，时长来自 manifest 的 frame_ms。 */
export function buildSequenceAnimation(
  packId: string,
  source: string,
  canvas: ResourceCanvas,
  frames: readonly RawAnimationFrame[],
): AnimationResource {
  return build("sequence", packId, source, canvas, frames);
}

/** GIF：单文件多帧，时长来自文件自带的逐帧延迟。 */
export function buildGifAnimation(
  packId: string,
  source: string,
  canvas: ResourceCanvas,
  frames: readonly RawAnimationFrame[],
): AnimationResource {
  return build("gif", packId, source, canvas, frames);
}

/** 静默主形象：单帧序列，不注册到任何状态。 */
export function buildStaticAnimation(
  packId: string,
  source: string,
  canvas: ResourceCanvas,
  frame: RawAnimationFrame,
): AnimationResource {
  return build("sequence", packId, source, canvas, [frame]);
}

export function animationDuration(animation: AnimationResource): number {
  return animation.frames.reduce((total, frame) => total + frame.durationMs, 0);
}

/** 按累计时长循环取当前帧；空资源返回 -1。 */
export function frameIndexAt(animation: AnimationResource, elapsedMs: number): number {
  const count = animation.frames.length;
  if (count === 0) return -1;
  const total = animationDuration(animation);
  if (total <= 0) return 0;
  let remaining = Math.max(0, elapsedMs) % total;
  for (let index = 0; index < count; index += 1) {
    const frame = animation.frames[index];
    if (remaining < frame.durationMs) return index;
    remaining -= frame.durationMs;
  }
  return count - 1;
}

export function frameAt(animation: AnimationResource, elapsedMs: number): AnimationFrame | null {
  const index = frameIndexAt(animation, elapsedMs);
  return index < 0 ? null : animation.frames[index];
}

/** 距离下一次换帧的毫秒数，用于按累计时间轴调度定时器。 */
export function timeToNextFrame(animation: AnimationResource, elapsedMs: number): number {
  const total = animationDuration(animation);
  if (total <= 0) return DEFAULT_FRAME_DELAY_MS;
  let remaining = Math.max(0, elapsedMs) % total;
  for (const frame of animation.frames) {
    if (remaining < frame.durationMs) return frame.durationMs - remaining;
    remaining -= frame.durationMs;
  }
  return total;
}

/** 锚点取资源自身矩形中心，与 alpha 包围盒无关。 */
export function resourceAnchor(canvas: ResourceCanvas): { x: number; y: number } {
  return { x: canvas.width / 2, y: canvas.height / 2 };
}
