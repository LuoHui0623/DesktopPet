import { describe, expect, it } from "vitest";
import type { AvatarPack, AvatarsResponse } from "../../src/api/types";
import {
  avatarCandidates,
  avatarFrameAt,
  avatarFramePath,
  avatarStateKey,
  selectAvatarAnimation,
} from "../../src/modules/pet/avatar-view";

function pack(id: string, overrides: Partial<AvatarPack> = {}): AvatarPack {
  return {
    id,
    name: id,
    active: false,
    frame: { width: 240, height: 240 },
    fallback: "idle",
    states: {
      idle: { frames: ["idle.png"], frame_ms: 1000 },
      disconnected: { frames: ["disconnected.png"], frame_ms: 500 },
    },
    ...overrides,
  };
}

function catalog(active: string, packs: AvatarPack[]): AvatarsResponse {
  return { active, packs };
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

  it("returns nothing when neither candidate exists", () => {
    expect(avatarCandidates(catalog("gone", [pack("other")]))).toEqual([]);
    expect(avatarCandidates(catalog("default", []))).toEqual([]);
  });
});

describe("avatarStateKey", () => {
  it("maps a missing state to the disconnected frames", () => {
    expect(avatarStateKey(null)).toBe("disconnected");
    expect(avatarStateKey("thinking")).toBe("thinking");
  });
});

describe("selectAvatarAnimation", () => {
  it("selects the whole frame sequence of the current state", () => {
    const subject = pack("default", {
      states: {
        idle: { frames: ["idle.png"], frame_ms: 1000 },
        thinking: { frames: ["thinking_a.png", "thinking_b.png"], frame_ms: 250 },
      },
    });

    expect(selectAvatarAnimation(subject, "thinking")).toEqual({
      frames: ["thinking_a.png", "thinking_b.png"],
      frameMs: 250,
    });
  });

  it("uses the disconnected frames when there is no state", () => {
    expect(selectAvatarAnimation(pack("default"), null)).toEqual({
      frames: ["disconnected.png"],
      frameMs: 500,
    });
  });

  it("falls back to the manifest fallback state for unknown states", () => {
    const subject = pack("default", {
      states: {
        idle: { frames: ["idle.png"], frame_ms: 1000 },
        disconnected: { frames: ["off.png"], frame_ms: 500 },
      },
    });

    expect(selectAvatarAnimation(subject, "sleeping")).toEqual({
      frames: ["idle.png"],
      frameMs: 1000,
    });
  });

  it("returns null when neither the state nor the fallback exists", () => {
    const subject = pack("default", {
      fallback: "idle",
      states: { speaking: { frames: ["speak.png"], frame_ms: 1000 } },
    });

    expect(selectAvatarAnimation(subject, "executing")).toBeNull();
  });
});

describe("avatarFrameAt", () => {
  const animation = { frames: ["a.png", "b.png", "c.png"], frameMs: 120 };

  it("plays frames in order", () => {
    expect(avatarFrameAt(animation, 0)).toBe("a.png");
    expect(avatarFrameAt(animation, 2)).toBe("c.png");
  });

  it("loops back to the first frame", () => {
    expect(avatarFrameAt(animation, 3)).toBe("a.png");
    expect(avatarFrameAt(animation, 7)).toBe("b.png");
  });
});

describe("avatarFramePath", () => {
  it("encodes the pack id and nested frame segments", () => {
    expect(avatarFramePath("default", "frames/idle 1.png")).toBe(
      "/api/avatars/default/frames/idle%201.png",
    );
  });
});
