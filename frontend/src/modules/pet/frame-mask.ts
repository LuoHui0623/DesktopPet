/** 命中与穿透判定使用的 alpha 阈值；显示仍直接使用图像自身 alpha。 */
export const HIT_ALPHA_THRESHOLD = 32;

export interface MaskBounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface FrameMask {
  width: number;
  height: number;
  rowBytes: number;
  threshold: number;
  bits: Uint8Array;
  bounds: MaskBounds | null;
  hash: string;
}

function fnv1a(bytes: Uint8Array, seed = 0x811c9dc5): number {
  let hash = seed >>> 0;
  for (const byte of bytes) {
    hash ^= byte;
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

export function hashMask(
  width: number,
  height: number,
  threshold: number,
  bits: Uint8Array,
): string {
  const header = new Uint8Array(12);
  const view = new DataView(header.buffer);
  view.setUint32(0, width, true);
  view.setUint32(4, height, true);
  view.setUint32(8, threshold, true);
  let hash = fnv1a(header);
  hash = fnv1a(bits, hash);
  return hash.toString(16).padStart(8, "0");
}

/** 从 RGBA 像素生成 1bpp 命中掩码；整帧透明时返回 null。 */
export function buildFrameMask(
  rgba: Uint8ClampedArray,
  width: number,
  height: number,
  threshold: number = HIT_ALPHA_THRESHOLD,
): FrameMask | null {
  if (width <= 0 || height <= 0 || rgba.length < width * height * 4) return null;
  const rowBytes = Math.ceil(width / 8);
  const bits = new Uint8Array(rowBytes * height);
  let left = width;
  let right = -1;
  let top = height;
  let bottom = -1;
  for (let y = 0; y < height; y += 1) {
    const row = y * rowBytes;
    for (let x = 0; x < width; x += 1) {
      if (rgba[(y * width + x) * 4 + 3] <= threshold) continue;
      bits[row + (x >> 3)] |= 1 << (x & 7);
      if (x < left) left = x;
      if (x > right) right = x;
      if (y < top) top = y;
      if (y > bottom) bottom = y;
    }
  }
  if (right < 0) return null;
  return {
    width,
    height,
    rowBytes,
    threshold,
    bits,
    bounds: { left, top, right, bottom },
    hash: hashMask(width, height, threshold, bits),
  };
}

export function maskHasPixel(mask: FrameMask, x: number, y: number): boolean {
  if (x < 0 || y < 0 || x >= mask.width || y >= mask.height) return false;
  return (mask.bits[y * mask.rowBytes + (x >> 3)] & (1 << (x & 7))) !== 0;
}

function boundsFromBits(
  bits: Uint8Array,
  width: number,
  height: number,
  rowBytes: number,
): MaskBounds | null {
  let left = width;
  let right = -1;
  let top = height;
  let bottom = -1;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if ((bits[y * rowBytes + (x >> 3)] & (1 << (x & 7))) === 0) continue;
      if (x < left) left = x;
      if (x > right) right = x;
      if (y < top) top = y;
      if (y > bottom) bottom = y;
    }
  }
  return right < 0 ? null : { left, top, right, bottom };
}

const MASK_HEADER_BYTES = 16;

/** 位图编码：宽、高、行字节、阈值各 4 字节，后接按行优先的位图。 */
export function encodeFrameMask(mask: FrameMask): Uint8Array {
  const bytes = new Uint8Array(MASK_HEADER_BYTES + mask.bits.length);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, mask.width, true);
  view.setUint32(4, mask.height, true);
  view.setUint32(8, mask.rowBytes, true);
  view.setUint32(12, mask.threshold, true);
  bytes.set(mask.bits, MASK_HEADER_BYTES);
  return bytes;
}

export function decodeFrameMask(bytes: Uint8Array): FrameMask | null {
  if (bytes.length < MASK_HEADER_BYTES) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = view.getUint32(0, true);
  const height = view.getUint32(4, true);
  const rowBytes = view.getUint32(8, true);
  const threshold = view.getUint32(12, true);
  if (width <= 0 || height <= 0 || rowBytes !== Math.ceil(width / 8)) return null;
  if (bytes.length !== MASK_HEADER_BYTES + rowBytes * height) return null;
  const bits = bytes.slice(MASK_HEADER_BYTES);
  return {
    width,
    height,
    rowBytes,
    threshold,
    bits,
    bounds: boundsFromBits(bits, width, height, rowBytes),
    hash: hashMask(width, height, threshold, bits),
  };
}
