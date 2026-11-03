import { describe, expect, it } from "vitest";
import {
  EMPTY_RECT,
  clientToSource,
  containRect,
  offsetRect,
  pointInRect,
  rectCenter,
  transformOrigin,
} from "../../src/modules/pet/avatar-layout";

describe("containRect", () => {
  it("fits a square resource into a wide slot, centred horizontally and bottom-aligned", () => {
    expect(containRect({ width: 240, height: 240 }, 320, 120)).toEqual({
      left: 100,
      top: 0,
      width: 120,
      height: 120,
    });
  });

  it("fits a wide resource by height and centres it horizontally", () => {
    expect(containRect({ width: 400, height: 200 }, 300, 200)).toEqual({
      left: 0,
      top: 50,
      width: 300,
      height: 150,
    });
  });

  it("scales up to fill the limiting axis", () => {
    expect(containRect({ width: 100, height: 50 }, 400, 400)).toEqual({
      left: 0,
      top: 200,
      width: 400,
      height: 200,
    });
  });

  it("returns an empty rect for an empty resource or slot", () => {
    expect(containRect({ width: 0, height: 10 }, 100, 100)).toEqual(EMPTY_RECT);
    expect(containRect({ width: 10, height: 10 }, 0, 100)).toEqual(EMPTY_RECT);
  });
});

describe("offsetRect", () => {
  it("moves the local rect into window coordinates", () => {
    expect(offsetRect({ x: 10, y: 20 }, { left: 5, top: 6, width: 7, height: 8 })).toEqual({
      left: 15,
      top: 26,
      width: 7,
      height: 8,
    });
  });
});

describe("pointInRect", () => {
  const rect = { left: 10, top: 20, width: 30, height: 40 };

  it("includes the edges", () => {
    expect(pointInRect({ x: 10, y: 20 }, rect)).toBe(true);
    expect(pointInRect({ x: 40, y: 60 }, rect)).toBe(true);
  });

  it("excludes points outside", () => {
    expect(pointInRect({ x: 9, y: 20 }, rect)).toBe(false);
    expect(pointInRect({ x: 41, y: 30 }, rect)).toBe(false);
  });

  it("grows the rect by the padding", () => {
    expect(pointInRect({ x: 5, y: 15 }, rect, 5)).toBe(true);
    expect(pointInRect({ x: 45, y: 65 }, rect, 5)).toBe(true);
    expect(pointInRect({ x: 4, y: 15 }, rect, 5)).toBe(false);
  });
});

describe("clientToSource", () => {
  const drawRect = { left: 100, top: 0, width: 120, height: 120 };

  it("maps window coordinates back to resource pixels", () => {
    expect(clientToSource(drawRect, 240, 240, 100, 0)).toEqual({ x: 0, y: 0 });
    expect(clientToSource(drawRect, 240, 240, 220, 120)).toEqual({ x: 239, y: 239 });
    expect(clientToSource(drawRect, 240, 240, 160, 60)).toEqual({ x: 120, y: 120 });
  });

  it("clamps the last pixel when the point is on the far edge", () => {
    expect(clientToSource(drawRect, 240, 240, 219.9, 60)).toEqual({ x: 239, y: 120 });
  });

  it("returns null outside the draw rect", () => {
    expect(clientToSource(drawRect, 240, 240, 99, 0)).toBeNull();
    expect(clientToSource(drawRect, 240, 240, 50, 60)).toBeNull();
  });

  it("returns null for degenerate input", () => {
    expect(clientToSource(drawRect, 0, 240, 110, 10)).toBeNull();
    expect(containRect({ width: 0, height: 0 }, 100, 100)).toEqual(EMPTY_RECT);
    expect(clientToSource(EMPTY_RECT, 240, 240, 0, 0)).toBeNull();
  });
});

describe("rectCenter / transformOrigin", () => {
  it("returns the rect centre and CSS origin string", () => {
    const rect = { left: 100, top: 0, width: 120, height: 120 };

    expect(rectCenter(rect)).toEqual({ x: 160, y: 60 });
    expect(transformOrigin(rect)).toBe("160px 60px");
  });
});
