import { describe, expect, it } from "vitest";
import type { AvatarPack, AvatarsResponse } from "../../src/api/types";
import { avatarCandidates, avatarFramePath } from "../../src/modules/pet/avatar-view";

const PERSONA = "芙芙";

function pack(id: string, overrides: Partial<AvatarPack> = {}): AvatarPack {
  return {
    id,
    name: id,
    active: false,
    frame: { width: 240, height: 240 },
    fallback: "idle",
    static: "static.png",
    states: {},
    ...overrides,
  };
}

function catalog(active: string | null, packs: AvatarPack[]): AvatarsResponse {
  return { persona: PERSONA, active, preset_states: ["idle", "thinking", "disconnected"], packs };
}

describe("avatarCandidates", () => {
  it("tries the active pack first and falls back to the default pack", () => {
    const active = pack("custom");
    const fallback = pack("default");
    expect(avatarCandidates(catalog("custom", [fallback, active]))).toEqual([active, fallback]);
  });

  it("does not repeat the active pack when it is the default one", () => {
    const fallback = pack("default");
    expect(avatarCandidates(catalog("default", [fallback]))).toEqual([fallback]);
  });

  it("falls back to the default pack when the persona has no remembered pack", () => {
    const fallback = pack("default");
    expect(avatarCandidates(catalog(null, [fallback]))).toEqual([fallback]);
  });

  it("returns nothing when neither candidate exists", () => {
    expect(avatarCandidates(catalog("gone", [pack("other")]))).toEqual([]);
    expect(avatarCandidates(catalog(null, []))).toEqual([]);
  });
});

describe("avatarFramePath", () => {
  it("puts the persona and pack before the nested frame segments", () => {
    expect(avatarFramePath(PERSONA, "default", "frames/idle 1.png")).toBe(
      `/api/avatars/${encodeURIComponent(PERSONA)}/default/frames/idle%201.png`,
    );
  });

  it("encodes persona and pack ids as single path segments", () => {
    expect(avatarFramePath("a/b", "c/d", "idle.png")).toBe("/api/avatars/a%2Fb/c%2Fd/idle.png");
  });
});
