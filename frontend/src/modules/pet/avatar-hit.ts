import { clientToSource, pointInRect, type LayoutRect, type Point } from "./avatar-layout";
import { maskHasPixel, type FrameMask } from "./frame-mask";

export interface FrameViewport {
  mask: FrameMask | null;
  drawRect: LayoutRect;
  sourceWidth: number;
  sourceHeight: number;
}

export interface InteractiveRect {
  rect: LayoutRect;
  padding?: number;
}

/** 形象像素命中；没有掩码（加载中、静态图损坏或整块 GIF）时按整块命中。 */
export function isAvatarPixel(frame: FrameViewport | null, point: Point): boolean {
  if (frame === null || frame.mask === null) return true;
  const source = clientToSource(
    frame.drawRect,
    frame.sourceWidth,
    frame.sourceHeight,
    point.x,
    point.y,
  );
  return source !== null && maskHasPixel(frame.mask, source.x, source.y);
}

/**
 * 可交互 = 当前帧掩码 ∪ 标签矩形 ∪ 气泡矩形（含 padding）。
 * 没有帧信息时整窗可交互，避免加载期点不到。
 */
export function isInteractiveAt(
  frame: FrameViewport | null,
  uiRects: readonly InteractiveRect[],
  point: Point,
): boolean {
  for (const entry of uiRects) {
    if (pointInRect(point, entry.rect, entry.padding ?? 0)) return true;
  }
  return isAvatarPixel(frame, point);
}
