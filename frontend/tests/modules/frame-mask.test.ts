import { describe, expect, it } from "vitest";
import {
  HIT_ALPHA_THRESHOLD,
  buildFrameMask,
  decodeFrameMask,
  encodeFrameMask,
  hashMask,
  maskHasPixel,
} from "../../src/modules/pet/frame-mask";

function rgba(width: number, height: number, alphas: number[]): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4);
  alphas.forEach((alpha, index) => {
    data[index * 4 + 0] = 255;
    data[index * 4 + 3] = alpha;
  });
  return data;
}

describe("buildFrameMask", () => {
  it("marks only pixels above the hit threshold", () => {
    const mask = buildFrameMask(rgba(2, 1, [0, HIT_ALPHA_THRESHOLD + 1]), 2, 1);

    expect(mask).not.toBeNull();
    expect(maskHasPixel(mask!, 0, 0)).toBe(false);
    expect(maskHasPixel(mask!, 1, 0)).toBe(true);
  });

  it("treats alpha equal to the threshold as transparent on both sides", () => {
    const mask = buildFrameMask(rgba(1, 1, [HIT_ALPHA_THRESHOLD]), 1, 1);
    expect(mask).toBeNull();
  });

  it("returns null for an all-transparent frame", () => {
    expect(buildFrameMask(rgba(4, 4, new Array(16).fill(0)), 4, 4)).toBeNull();
  });

  it("rejects a buffer shorter than the declared size", () => {
    expect(buildFrameMask(new Uint8ClampedArray(3), 2, 2)).toBeNull();
  });

  it("tracks the opaque bounds in source pixels", () => {
    const mask = buildFrameMask(rgba(4, 3, [0, 0, 0, 0, 0, 255, 255, 0, 0, 0, 255, 0]), 4, 3);

    expect(mask?.bounds).toEqual({ left: 1, top: 1, right: 2, bottom: 2 });
  });

  it("keeps the row packing and threshold on the mask", () => {
    const mask = buildFrameMask(rgba(9, 1, new Array(9).fill(255)), 9, 1);

    expect(mask?.rowBytes).toBe(2);
    expect(mask?.threshold).toBe(HIT_ALPHA_THRESHOLD);
    expect(maskHasPixel(mask!, 8, 0)).toBe(true);
  });

  it("ignores out-of-range lookups", () => {
    const mask = buildFrameMask(rgba(2, 2, new Array(4).fill(255)), 2, 2)!;

    expect(maskHasPixel(mask, -1, 0)).toBe(false);
    expect(maskHasPixel(mask, 0, -1)).toBe(false);
    expect(maskHasPixel(mask, 2, 0)).toBe(false);
    expect(maskHasPixel(mask, 0, 2)).toBe(false);
  });
});

describe("hashMask", () => {
  it("is stable and sensitive to the pixels and header", () => {
    const bits = new Uint8Array([0b0000_0001]);
    const same = hashMask(8, 1, 32, bits);

    expect(hashMask(8, 1, 32, new Uint8Array([0b0000_0001]))).toBe(same);
    expect(hashMask(8, 1, 32, new Uint8Array([0b0000_0010]))).not.toBe(same);
    expect(hashMask(8, 1, 16, bits)).not.toBe(same);
    expect(hashMask(9, 1, 32, bits)).not.toBe(same);
  });
});

describe("encodeFrameMask / decodeFrameMask", () => {
  it("round-trips the mask geometry, bits and hash", () => {
    const original = buildFrameMask(rgba(4, 3, [0, 0, 0, 0, 0, 255, 255, 0, 0, 0, 255, 0]), 4, 3)!;

    const decoded = decodeFrameMask(encodeFrameMask(original));

    expect(decoded).toEqual(original);
  });

  it("rejects short headers", () => {
    expect(decodeFrameMask(new Uint8Array(15))).toBeNull();
  });

  it("rejects an inconsistent row byte count", () => {
    const bytes = encodeFrameMask(buildFrameMask(rgba(9, 1, new Array(9).fill(255)), 9, 1)!);
    new DataView(bytes.buffer).setUint32(8, 1, true);

    expect(decodeFrameMask(bytes)).toBeNull();
  });

  it("rejects a payload with the wrong length", () => {
    const bytes = encodeFrameMask(buildFrameMask(rgba(2, 2, new Array(4).fill(255)), 2, 2)!);
    expect(decodeFrameMask(bytes.slice(0, bytes.length - 1))).toBeNull();
  });
});
