export interface LayoutRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface LayoutSize {
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export const EMPTY_RECT: LayoutRect = { left: 0, top: 0, width: 0, height: 0 };

/**
 * 按 contain 把资源画布放进槽位：水平居中、底边贴齐槽位底部。
 * 绘制、命中与穿透判定都使用这个矩形。
 */
export function containRect(canvas: LayoutSize, slotWidth: number, slotHeight: number): LayoutRect {
  if (canvas.width <= 0 || canvas.height <= 0 || slotWidth <= 0 || slotHeight <= 0) {
    return EMPTY_RECT;
  }
  const scale = Math.min(slotWidth / canvas.width, slotHeight / canvas.height);
  const width = canvas.width * scale;
  const height = canvas.height * scale;
  return {
    left: (slotWidth - width) / 2,
    top: slotHeight - height,
    width,
    height,
  };
}

/** 把槽位内的局部矩形换算成窗口内坐标。 */
export function offsetRect(origin: Point, local: LayoutRect): LayoutRect {
  return {
    left: origin.x + local.left,
    top: origin.y + local.top,
    width: local.width,
    height: local.height,
  };
}

export function pointInRect(point: Point, rect: LayoutRect, padding = 0): boolean {
  return (
    point.x >= rect.left - padding &&
    point.x <= rect.left + rect.width + padding &&
    point.y >= rect.top - padding &&
    point.y <= rect.top + rect.height + padding
  );
}

/** 把窗口内坐标换算回资源自身像素；落在绘制矩形外时返回 null。 */
export function clientToSource(
  drawRect: LayoutRect,
  sourceWidth: number,
  sourceHeight: number,
  clientX: number,
  clientY: number,
): Point | null {
  if (drawRect.width <= 0 || drawRect.height <= 0 || sourceWidth <= 0 || sourceHeight <= 0) {
    return null;
  }
  if (!pointInRect({ x: clientX, y: clientY }, drawRect)) return null;
  const x = Math.floor(((clientX - drawRect.left) / drawRect.width) * sourceWidth);
  const y = Math.floor(((clientY - drawRect.top) / drawRect.height) * sourceHeight);
  return {
    x: Math.min(Math.max(x, 0), sourceWidth - 1),
    y: Math.min(Math.max(y, 0), sourceHeight - 1),
  };
}

/** 反应动画的 transform-origin：绘制矩形中心。 */
export function rectCenter(rect: LayoutRect): Point {
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

export function transformOrigin(rect: LayoutRect): string {
  const center = rectCenter(rect);
  return `${center.x}px ${center.y}px`;
}
