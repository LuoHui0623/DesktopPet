import { describe, expect, it } from "vitest";
import {
  ALPHA_THRESHOLD,
  boundsFromAlpha,
  centerOffset,
  isOpaqueAt,
} from "../../src/modules/pet/pet-alpha";

/** 造一张 width×height 的 alpha 图，指定矩形区域为不透明。 */
function makeAlpha(
  width: number,
  height: number,
  rect?: { left: number; top: number; right: number; bottom: number },
): Uint8ClampedArray {
  const alpha = new Uint8ClampedArray(width * height);
  if (rect === undefined) return alpha;
  for (let y = rect.top; y <= rect.bottom; y += 1) {
    for (let x = rect.left; x <= rect.right; x += 1) {
      alpha[y * width + x] = 255;
    }
  }
  return alpha;
}

describe("boundsFromAlpha", () => {
  it("返回不透明像素的外接包围盒", () => {
    const alpha = makeAlpha(10, 8, { left: 2, top: 1, right: 6, bottom: 5 });
    expect(boundsFromAlpha(alpha, 10, 8)).toEqual({ left: 2, top: 1, right: 6, bottom: 5 });
  });

  it("整张全透明时返回 null", () => {
    expect(boundsFromAlpha(makeAlpha(10, 8), 10, 8)).toBeNull();
  });

  it("忽略低于阈值的极淡噪点", () => {
    const alpha = makeAlpha(10, 8, { left: 3, top: 3, right: 4, bottom: 4 });
    alpha[0] = ALPHA_THRESHOLD;
    alpha[9] = ALPHA_THRESHOLD;
    const bounds = boundsFromAlpha(alpha, 10, 8);
    expect(bounds).not.toBeNull();
    expect(bounds?.left).toBe(3);
    expect(bounds?.right).toBe(4);
  });
});

describe("centerOffset", () => {
  it("轮廓本身居中时不需要平移", () => {
    // 10×10 图上 2..7 的轮廓正好居中
    const bounds = { left: 2, top: 2, right: 7, bottom: 7 };
    expect(centerOffset(bounds, 10, 100)).toBeCloseTo(0);
  });

  it("轮廓偏左时向右补正", () => {
    // 10×10 图上 0..5 的轮廓中心在 x=3，偏左 2px（源图），缩放到 10 倍后需右移 20px
    const bounds = { left: 0, top: 0, right: 5, bottom: 5 };
    expect(centerOffset(bounds, 10, 100)).toBeCloseTo(20);
  });

  it("没有轮廓或源图宽度非法时不平移", () => {
    expect(centerOffset(null, 10, 100)).toBe(0);
    expect(centerOffset({ left: 0, top: 0, right: 5, bottom: 5 }, 0, 100)).toBe(0);
  });
});

describe("isOpaqueAt", () => {
  const alpha = makeAlpha(10, 8, { left: 2, top: 1, right: 6, bottom: 5 });

  it("不透明像素命中", () => {
    expect(isOpaqueAt(alpha, 10, 8, 4, 3)).toBe(true);
  });

  it("透明像素不命中", () => {
    expect(isOpaqueAt(alpha, 10, 8, 0, 0)).toBe(false);
    expect(isOpaqueAt(alpha, 10, 8, 9, 7)).toBe(false);
  });

  it("越界坐标不命中", () => {
    expect(isOpaqueAt(alpha, 10, 8, -1, 3)).toBe(false);
    expect(isOpaqueAt(alpha, 10, 8, 10, 3)).toBe(false);
    expect(isOpaqueAt(alpha, 10, 8, 4, 8)).toBe(false);
  });
});
