import { buildFrameMask, type FrameMask } from "./frame-mask";

export interface DecodedAssetFrame {
  image: CanvasImageSource | null;
  width: number;
  height: number;
  durationMs: number;
  mask: FrameMask | null;
}

export interface AvatarDecoder {
  decode: (source: string, blob: Blob) => Promise<readonly DecodedAssetFrame[]>;
}

function isGif(source: string): boolean {
  return source.toLowerCase().endsWith(".gif");
}

function readMask(image: CanvasImageSource, width: number, height: number): FrameMask | null {
  if (typeof document === "undefined" || width <= 0 || height <= 0) return null;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (context === null) return null;
  context.clearRect(0, 0, width, height);
  context.drawImage(image, 0, 0);
  const data = context.getImageData(0, 0, width, height).data;
  return buildFrameMask(data, width, height);
}

async function decodeStill(blob: Blob): Promise<DecodedAssetFrame> {
  const image = await createImageBitmap(blob);
  const frame = {
    image,
    width: image.width,
    height: image.height,
    durationMs: 0,
    mask: readMask(image, image.width, image.height),
  };
  return frame;
}

async function decodeGif(blob: Blob): Promise<DecodedAssetFrame[]> {
  if (typeof ImageDecoder === "undefined") {
    throw new Error("当前环境不支持 ImageDecoder，无法逐帧解码 GIF");
  }
  const decoder = new ImageDecoder({
    data: await blob.arrayBuffer(),
    type: "image/gif",
    preferAnimation: true,
  });
  try {
    await decoder.completed;
    const track = decoder.tracks.selectedTrack;
    const count = track?.frameCount ?? 0;
    const frames: DecodedAssetFrame[] = [];
    for (let index = 0; index < count; index += 1) {
      const result = await decoder.decode({ frameIndex: index, completeFramesOnly: true });
      const videoFrame = result.image;
      const image = await createImageBitmap(videoFrame);
      const width = videoFrame.displayWidth || image.width;
      const height = videoFrame.displayHeight || image.height;
      const durationMs = (videoFrame.duration ?? 0) / 1000;
      videoFrame.close();
      frames.push({
        image,
        width,
        height,
        durationMs,
        mask: readMask(image, width, height),
      });
    }
    return frames;
  } finally {
    decoder.close();
  }
}

/** 浏览器解码适配器：GIF 逐帧输出，PNG / WebP 单帧输出；不做 disposal 合成。 */
export function createBrowserAvatarDecoder(): AvatarDecoder {
  return {
    decode: (source, blob) =>
      isGif(source) ? decodeGif(blob) : decodeStill(blob).then((f) => [f]),
  };
}
