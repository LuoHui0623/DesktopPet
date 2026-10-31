import { boundsFromAlpha, isOpaqueAt, type AlphaBounds } from "./pet-alpha";

export interface AvatarAlpha {
  width: number;
  height: number;
  bounds: AlphaBounds | null;
  isOpaqueAt: (x: number, y: number) => boolean;
}

const cache = new Map<string, Promise<AvatarAlpha | null>>();

async function readAlpha(blobUrl: string): Promise<AvatarAlpha | null> {
  const image = new Image();
  image.src = blobUrl;
  try {
    await image.decode();
  } catch {
    return null;
  }
  const width = image.naturalWidth;
  const height = image.naturalHeight;
  if (width === 0 || height === 0) return null;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (context === null) return null;
  context.drawImage(image, 0, 0);
  const data = context.getImageData(0, 0, width, height).data;
  const alpha = new Uint8ClampedArray(width * height);
  for (let index = 0; index < alpha.length; index += 1) {
    alpha[index] = data[index * 4 + 3];
  }
  return {
    width,
    height,
    bounds: boundsFromAlpha(alpha, width, height),
    isOpaqueAt: (x, y) => isOpaqueAt(alpha, width, height, x, y),
  };
}

/** 读取形象帧的 alpha 通道；帧地址是同一来源的 blob URL，可按地址缓存。 */
export function loadAvatarAlpha(blobUrl: string): Promise<AvatarAlpha | null> {
  const cached = cache.get(blobUrl);
  if (cached !== undefined) return cached;
  const loading = readAlpha(blobUrl);
  cache.set(blobUrl, loading);
  return loading;
}
