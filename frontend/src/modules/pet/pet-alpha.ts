/** 不透明像素的外接包围盒，坐标为原始图片的整像素行列号（含端点）。 */
export interface AlphaBounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** 低于该 alpha 值的像素视为透明，避开导出图片边缘的极淡噪点。 */
export const ALPHA_THRESHOLD = 8;

/** 扫描整块 alpha 通道，返回有色边界；整张全透明时返回 null。 */
export function boundsFromAlpha(
  alpha: Uint8ClampedArray,
  width: number,
  height: number,
  threshold: number = ALPHA_THRESHOLD,
): AlphaBounds | null {
  let left = width;
  let right = -1;
  let top = height;
  let bottom = -1;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (alpha[y * width + x] <= threshold) continue;
      if (x < left) left = x;
      if (x > right) right = x;
      if (y < top) top = y;
      if (y > bottom) bottom = y;
    }
  }
  if (right < 0) return null;
  return { left, top, right, bottom };
}

/**
 * 让有色边界水平居中所需的平移量（渲染像素）。
 *
 * 图片按 `renderedWidth / sourceWidth` 等比缩放后，若轮廓左右留白不对称，
 * 图片盒居中并不等于轮廓居中，这里补齐差值。
 */
export function centerOffset(
  bounds: AlphaBounds | null,
  sourceWidth: number,
  renderedWidth: number,
): number {
  if (bounds === null || sourceWidth <= 0) return 0;
  const scale = renderedWidth / sourceWidth;
  const outlineCenter = ((bounds.left + bounds.right + 1) / 2) * scale;
  return renderedWidth / 2 - outlineCenter;
}

/** 判断原始图片坐标上的像素是否不透明，用于点击与悬停命中。 */
export function isOpaqueAt(
  alpha: Uint8ClampedArray,
  width: number,
  height: number,
  x: number,
  y: number,
  threshold: number = ALPHA_THRESHOLD,
): boolean {
  if (x < 0 || y < 0 || x >= width || y >= height) return false;
  return alpha[y * width + x] > threshold;
}
