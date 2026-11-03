import { describe, expect, it } from "vitest";
import {
  isAvatarPixel,
  isInteractiveAt,
  type FrameViewport,
} from "../../src/modules/pet/avatar-hit";
import { buildFrameMask } from "../../src/modules/pet/frame-mask";

function maskFrom(alphas: number[], width: number, height: number) {
  const data = new Uint8ClampedArray(width * height * 4);
  alphas.forEach((alpha, index) => {
    data[index * 4 + 3] = alpha;
  });
  return buildFrameMask(data, width, height);
}

/** 4×4 资源，绘制在窗口 (100, 50) 处的 40×40 矩形，源像素 10×10。 */
const viewport: FrameViewport = {
  mask: maskFrom([0, 0, 0, 0, 0, 255, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 4, 4),
  drawRect: { left: 100, top: 50, width: 40, height: 40 },
  sourceWidth: 4,
  sourceHeight: 4,
};

describe("isAvatarPixel", () => {
  it("hits an opaque source pixel", () => {
    expect(isAvatarPixel(viewport, { x: 115, y: 65 })).toBe(true);
  });

  it("misses transparent source pixels", () => {
    expect(isAvatarPixel(viewport, { x: 105, y: 55 })).toBe(false);
    expect(isAvatarPixel(viewport, { x: 125, y: 65 })).toBe(false);
  });

  it("misses points outside the draw rect", () => {
    expect(isAvatarPixel(viewport, { x: 90, y: 60 })).toBe(false);
  });

  it("treats a missing mask as whole-resource interactive", () => {
    expect(isAvatarPixel({ ...viewport, mask: null }, { x: 0, y: 0 })).toBe(true);
  });

  it("treats a missing frame as interactive", () => {
    expect(isAvatarPixel(null, { x: 0, y: 0 })).toBe(true);
  });
});

describe("isInteractiveAt", () => {
  const label = { rect: { left: 10, top: 200, width: 60, height: 20 } };
  const bubble = {
    rect: { left: 200, top: 10, width: 80, height: 40 },
    padding: 10,
  };

  it("hits avatar pixels", () => {
    expect(isInteractiveAt(viewport, [label], { x: 115, y: 65 })).toBe(true);
  });

  it("hits the label and padded bubble rects", () => {
    expect(isInteractiveAt(viewport, [label], { x: 30, y: 210 })).toBe(true);
    expect(isInteractiveAt(viewport, [label, bubble], { x: 195, y: 5 })).toBe(true);
    expect(isInteractiveAt(viewport, [label], { x: 195, y: 5 })).toBe(false);
  });

  it("misses everything transparent", () => {
    expect(isInteractiveAt(viewport, [label, bubble], { x: 50, y: 100 })).toBe(false);
  });

  it("is interactive everywhere without frame information", () => {
    expect(isInteractiveAt(null, [label], { x: 50, y: 100 })).toBe(true);
  });
});
