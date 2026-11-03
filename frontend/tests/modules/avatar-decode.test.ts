import { afterEach, describe, expect, it, vi } from "vitest";
import { createBrowserAvatarDecoder } from "../../src/modules/pet/avatar-decode";

interface FakeVideoFrame {
  displayWidth: number;
  displayHeight: number;
  duration: number;
  close: ReturnType<typeof vi.fn>;
}

class FakeImageDecoder {
  static instances: FakeImageDecoder[] = [];

  readonly completed = Promise.resolve();
  readonly tracks: { selectedTrack: { frameCount: number } | null } = {
    selectedTrack: { frameCount: 2 },
  };
  readonly decode = vi.fn(async ({ frameIndex }: { frameIndex: number }) => ({
    image: fakeVideoFrame((frameIndex + 1) * 100),
  }));
  readonly close = vi.fn();

  constructor(readonly init: unknown) {
    FakeImageDecoder.instances.push(this);
  }
}

function fakeVideoFrame(durationMs: number): FakeVideoFrame {
  return {
    displayWidth: 240,
    displayHeight: 240,
    duration: durationMs * 1000,
    close: vi.fn(),
  };
}

function blob(bytes = [1, 2, 3]): Blob {
  return new Blob([new Uint8Array(bytes)]);
}

const bitmap = vi.fn(async (source: { width?: number } = {}) => ({
  width: source.width ?? 240,
  height: 240,
}));

afterEach(() => {
  FakeImageDecoder.instances = [];
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("createBrowserAvatarDecoder", () => {
  it("decodes a still image into one frame with a zero duration", async () => {
    vi.stubGlobal("createImageBitmap", bitmap);
    const decoder = createBrowserAvatarDecoder();

    const frames = await decoder.decode("static.png", blob());

    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({ width: 240, height: 240, durationMs: 0, mask: null });
    expect(bitmap).toHaveBeenCalledTimes(1);
  });

  it("decodes every GIF frame with its own delay and closes the frames", async () => {
    vi.stubGlobal("createImageBitmap", bitmap);
    vi.stubGlobal("ImageDecoder", FakeImageDecoder);
    const decoder = createBrowserAvatarDecoder();

    const frames = await decoder.decode("frames/idle.gif", blob([71, 73, 70]));

    const instance = FakeImageDecoder.instances[0];
    expect(instance.init).toMatchObject({ type: "image/gif", preferAnimation: true });
    expect(instance.decode).toHaveBeenCalledTimes(2);
    expect(instance.close).toHaveBeenCalledTimes(1);
    expect(frames.map((frame) => frame.durationMs)).toEqual([100, 200]);
    expect(frames.map((frame) => [frame.width, frame.height])).toEqual([
      [240, 240],
      [240, 240],
    ]);
  });

  it("closes each intermediate video frame", async () => {
    const created: FakeVideoFrame[] = [];
    class TrackingDecoder extends FakeImageDecoder {
      readonly decode = vi.fn(async ({ frameIndex }: { frameIndex: number }) => {
        const frame = fakeVideoFrame(frameIndex + 1);
        created.push(frame);
        return { image: frame };
      });
    }

    vi.stubGlobal("createImageBitmap", bitmap);
    vi.stubGlobal("ImageDecoder", TrackingDecoder);

    await createBrowserAvatarDecoder().decode("idle.GIF", blob());

    expect(created.map((frame) => frame.close)).toHaveLength(2);
    for (const frame of created) expect(frame.close).toHaveBeenCalledTimes(1);
  });

  it("fails when the runtime has no ImageDecoder", async () => {
    vi.stubGlobal("createImageBitmap", bitmap);

    await expect(createBrowserAvatarDecoder().decode("idle.gif", blob())).rejects.toThrow(
      "ImageDecoder",
    );
  });

  it("treats a missing track as an empty animation", async () => {
    class EmptyDecoder extends FakeImageDecoder {
      readonly tracks = { selectedTrack: null };
    }

    vi.stubGlobal("createImageBitmap", bitmap);
    vi.stubGlobal("ImageDecoder", EmptyDecoder);

    await expect(createBrowserAvatarDecoder().decode("idle.gif", blob())).resolves.toEqual([]);
  });
});
