import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../../src/api/client";
import type { AvatarPack, AvatarsResponse } from "../../src/api/types";
import { loadAvatar, releaseAvatar } from "../../src/service/avatar.service";
import type { AvatarDecoder, DecodedAssetFrame } from "../../src/modules/pet/avatar-decode";
import { avatarStateKey, selectAvatarAnimation } from "../../src/modules/pet/avatar-state";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

function pack(id: string, overrides: Partial<AvatarPack> = {}): AvatarPack {
  return {
    id,
    name: id,
    active: false,
    frame: { width: 240, height: 240 },
    fallback: "idle",
    static: "static.png",
    states: {
      idle: { frames: ["idle.png"], frame_ms: 1000 },
    },
    ...overrides,
  };
}

const PERSONA = "芙芙";

const catalog: AvatarsResponse = {
  persona: PERSONA,
  active: "custom",
  preset_states: ["idle", "thinking", "disconnected"],
  packs: [pack("default"), pack("custom")],
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function makeClient(failingPacks: string[], body: unknown = catalog): ApiClient {
  const fetchImpl = vi.fn(async (input: string | URL | Request): Promise<Response> => {
    const url = String(input);
    if (url.endsWith("/api/avatars")) return jsonResponse(body);
    const packId = url.split("/api/avatars/")[1]?.split("/")[1] ?? "";
    if (failingPacks.includes(packId)) return new Response("missing", { status: 404 });
    return new Response(new Uint8Array([137, 80, 78, 71]), { status: 200 });
  });
  return {
    baseUrl: "http://127.0.0.1:4242",
    token: "token",
    fetch: fetchImpl as unknown as typeof fetch,
  };
}

function image(): CanvasImageSource & { close: () => void } {
  return { close: vi.fn() } as unknown as CanvasImageSource & { close: () => void };
}

function makeDecoder(
  framesFor: (source: string) => DecodedAssetFrame[] = (source) =>
    source.endsWith(".gif")
      ? [
          { image: image(), width: 240, height: 240, durationMs: 100, mask: null },
          { image: image(), width: 240, height: 240, durationMs: 10, mask: null },
        ]
      : [{ image: image(), width: 240, height: 240, durationMs: 0, mask: null }],
): AvatarDecoder {
  return { decode: vi.fn(async (source: string) => framesFor(source)) };
}

describe("loadAvatar", () => {
  it("loads the static main image first and then registers state animations", async () => {
    const client = makeClient([]);
    const events: string[] = [];

    const loaded = await loadAvatar(client, {
      decoder: makeDecoder(),
      onStatic: (preview) => {
        events.push(`static:${preview.animations.size}`);
      },
    });

    expect(loaded?.pack.id).toBe("custom");
    expect(events).toEqual(["static:0"]);
    expect(loaded?.staticResource?.source).toBe("static.png");
    expect(loaded?.staticResource?.frames[0].id).toBe("custom/static.png#0");
    expect(loaded?.animations.get("idle")?.frames[0].id).toBe("custom/idle.png#0");
    expect(loaded?.table.animations.has("static.png")).toBe(false);
    releaseAvatar(loaded as NonNullable<typeof loaded>);
  });

  it("requests the static file and every state frame with the token", async () => {
    const client = makeClient([]);

    const loaded = await loadAvatar(client, { decoder: makeDecoder() });

    const urls = vi.mocked(client.fetch).mock.calls.map((call) => String(call[0]));
    expect(urls).toContain(
      `http://127.0.0.1:4242/api/avatars/${encodeURIComponent(PERSONA)}/custom/static.png`,
    );
    expect(urls).toContain(
      `http://127.0.0.1:4242/api/avatars/${encodeURIComponent(PERSONA)}/custom/idle.png`,
    );
    const frameRequest = vi
      .mocked(client.fetch)
      .mock.calls.find((call) => String(call[0]).endsWith("static.png"));
    expect(frameRequest?.[1]?.headers).toEqual({ "X-Pet-Token": "token" });
    releaseAvatar(loaded as NonNullable<typeof loaded>);
  });

  it("uses the preset state list and ignores unregistered state names", async () => {
    const custom = pack("custom", {
      states: {
        idle: { frames: ["idle.png"], frame_ms: 1000 },
        dancing: { frames: ["dance.png"], frame_ms: 500 },
      },
    });

    const loaded = await loadAvatar(makeClient([], { ...catalog, packs: [custom] }), {
      decoder: makeDecoder(),
    });

    expect(loaded?.table.presetStates).toEqual(["idle", "thinking", "disconnected"]);
    expect(loaded?.animations.has("dancing")).toBe(false);
    expect(loaded?.table.animations.has("dancing")).toBe(false);
    releaseAvatar(loaded as NonNullable<typeof loaded>);
  });

  it("resolves a missing state through the fallback animation", async () => {
    const loaded = await loadAvatar(makeClient([]), { decoder: makeDecoder() });

    const selection = selectAvatarAnimation(loaded!.table, avatarStateKey("sleeping"));

    expect(selection?.source).toBe("fallback");
    expect(selection?.animation.frames[0].id).toBe("custom/idle.png#0");
    releaseAvatar(loaded as NonNullable<typeof loaded>);
  });

  it("builds GIF frames with file delays and normalized short delays", async () => {
    const gifPack = pack("custom", {
      states: {
        idle: { frames: ["idle.gif"], frame_ms: 0 },
      },
    });

    const loaded = await loadAvatar(makeClient([], { ...catalog, packs: [gifPack] }), {
      decoder: makeDecoder(),
    });

    const animation = loaded?.animations.get("idle");
    expect(animation?.kind).toBe("gif");
    expect(animation?.frames.map((frame) => frame.durationMs)).toEqual([100, 20]);
    expect(animation?.frames.map((frame) => frame.id)).toEqual([
      "custom/idle.gif#0",
      "custom/idle.gif#1",
    ]);
    releaseAvatar(loaded as NonNullable<typeof loaded>);
  });

  it("falls back to the default pack when the active pack has no usable static image", async () => {
    const loaded = await loadAvatar(makeClient(["custom"]), { decoder: makeDecoder() });

    expect(loaded?.pack.id).toBe("default");
    releaseAvatar(loaded as NonNullable<typeof loaded>);
  });

  it("returns null when no candidate pack can be loaded", async () => {
    expect(
      await loadAvatar(makeClient(["custom", "default"]), { decoder: makeDecoder() }),
    ).toBeNull();
  });

  it("returns null for an empty catalog", async () => {
    const empty = { persona: PERSONA, active: null, preset_states: ["idle"], packs: [] };

    expect(await loadAvatar(makeClient([], empty), { decoder: makeDecoder() })).toBeNull();
  });

  it("closes each decoded bitmap once when released", async () => {
    const shared = image();
    const decoder = makeDecoder((source) =>
      source.endsWith(".gif")
        ? []
        : [{ image: shared, width: 240, height: 240, durationMs: 0, mask: null }],
    );

    const loaded = await loadAvatar(makeClient([]), { decoder });
    releaseAvatar(loaded as NonNullable<typeof loaded>);

    expect((shared as unknown as { close: ReturnType<typeof vi.fn> }).close).toHaveBeenCalledTimes(
      1,
    );
  });

  it("decodes a file shared by several states once and keeps per-pack ids", async () => {
    const sharedPack = pack("custom", {
      states: {
        idle: { frames: ["shared.png"], frame_ms: 1000 },
        thinking: { frames: ["shared.png", "think.png"], frame_ms: 200 },
      },
    });
    const decoder = makeDecoder();

    const loaded = await loadAvatar(makeClient([], { ...catalog, packs: [sharedPack] }), {
      decoder,
    });

    const sources = vi.mocked(decoder.decode).mock.calls.map((call) => call[0]);
    expect(sources).toEqual(["static.png", "shared.png", "think.png"]);
    expect(loaded?.animations.get("idle")?.frames[0].id).toBe("custom/shared.png#0");
    expect(loaded?.animations.get("thinking")?.frames[0].id).toBe("custom/shared.png#0");
    expect(loaded?.animations.get("thinking")?.frames[1].id).toBe("custom/think.png#1");
    releaseAvatar(loaded as NonNullable<typeof loaded>);
  });

  it("skips a state whose frames fail to decode and keeps the fallback chain", async () => {
    const broken = pack("custom", {
      fallback: "thinking",
      states: {
        idle: { frames: ["broken.png"], frame_ms: 1000 },
        thinking: { frames: ["thinking.png"], frame_ms: 500 },
      },
    });
    const decoder: AvatarDecoder = {
      decode: vi.fn(async (source: string) => {
        if (source === "broken.png") throw new Error("decode failed");
        return [{ image: image(), width: 240, height: 240, durationMs: 0, mask: null }];
      }),
    };

    const loaded = await loadAvatar(makeClient([], { ...catalog, packs: [broken] }), { decoder });

    expect(loaded?.animations.has("idle")).toBe(false);
    expect(loaded?.animations.has("thinking")).toBe(true);
    const selection = selectAvatarAnimation(loaded!.table, "idle");
    expect(selection?.source).toBe("fallback");
    expect(selection?.animation.frames[0].id).toBe("custom/thinking.png#0");
    releaseAvatar(loaded as NonNullable<typeof loaded>);
  });
});
