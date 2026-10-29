import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../../src/api/client";
import type { AvatarPack, AvatarsResponse } from "../../src/api/types";
import { loadAvatar, releaseAvatar } from "../../src/service/avatar.service";

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

function pack(id: string): AvatarPack {
  return {
    id,
    name: id,
    active: false,
    frame: { width: 240, height: 240 },
    fallback: "idle",
    states: {
      idle: { frames: ["idle.png"], frame_ms: 1000 },
    },
  };
}

const catalog: AvatarsResponse = {
  active: "custom",
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
    const packId = url.split("/api/avatars/")[1]?.split("/")[0] ?? "";
    if (failingPacks.includes(packId)) return new Response("missing", { status: 404 });
    return new Response(new Uint8Array([137, 80, 78, 71]), { status: 200 });
  });
  return {
    baseUrl: "http://127.0.0.1:4242",
    token: "token",
    fetch: fetchImpl as unknown as typeof fetch,
  };
}

describe("loadAvatar", () => {
  it("loads the active pack with token-protected frame requests", async () => {
    const client = makeClient([]);

    const loaded = await loadAvatar(client);

    expect(loaded?.pack.id).toBe("custom");
    expect([...(loaded?.frameUrls.keys() ?? [])]).toEqual(["idle.png"]);
    const frameRequest = vi.mocked(client.fetch).mock.calls[1];
    expect(String(frameRequest[0])).toBe("http://127.0.0.1:4242/api/avatars/custom/idle.png");
    expect(frameRequest[1]?.headers).toEqual({ "X-Pet-Token": "token" });
    releaseAvatar(loaded as NonNullable<typeof loaded>);
  });

  it("falls back to the default pack when the active pack fails to load", async () => {
    const loaded = await loadAvatar(makeClient(["custom"]));

    expect(loaded?.pack.id).toBe("default");
  });

  it("returns null when no candidate pack can be loaded", async () => {
    expect(await loadAvatar(makeClient(["custom", "default"]))).toBeNull();
  });

  it("returns null for an empty catalog", async () => {
    const empty = { active: "default", packs: [] };

    expect(await loadAvatar(makeClient([], empty))).toBeNull();
  });
});
