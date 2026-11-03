import { describe, expect, it } from "vitest";
import {
  avatarStateKey,
  builtinAvatarResolvers,
  createAvatarBehaviorResolver,
  createAvatarStateTable,
  selectAvatarAnimation,
  type AvatarBehaviorContext,
} from "../../src/modules/pet/avatar-state";
import {
  buildSequenceAnimation,
  type AnimationResource,
} from "../../src/modules/pet/animation-resource";

const canvas = { width: 240, height: 240 };

function animation(source: string): AnimationResource {
  return buildSequenceAnimation("default", source, canvas, [
    { image: null, durationMs: 100, mask: null },
  ]);
}

const idle = animation("idle.png");
const thinking = animation("thinking.png");
const staticImage = animation("static.png");

function context(overrides: Partial<AvatarBehaviorContext> = {}): AvatarBehaviorContext {
  return { connected: true, status: "idle", ...overrides };
}

describe("avatarStateKey", () => {
  it("maps a missing chat state to disconnected", () => {
    expect(avatarStateKey(null)).toBe("disconnected");
    expect(avatarStateKey("thinking")).toBe("thinking");
  });
});

describe("createAvatarStateTable", () => {
  it("only keeps registered states and follows the preset order", () => {
    const table = createAvatarStateTable({
      presetStates: ["idle", "thinking", "disconnected"],
      fallbackState: "idle",
      animations: new Map([
        ["thinking", thinking],
        ["dancing", animation("dance.png")],
        ["idle", idle],
      ]),
      staticResource: staticImage,
    });

    expect([...table.animations.keys()]).toEqual(["idle", "thinking"]);
    expect(table.animations.get("dancing")).toBeUndefined();
    expect(table.staticResource).toBe(staticImage);
  });
});

describe("selectAvatarAnimation", () => {
  const table = createAvatarStateTable({
    presetStates: ["idle", "thinking", "disconnected"],
    fallbackState: "idle",
    animations: new Map([
      ["idle", idle],
      ["thinking", thinking],
    ]),
    staticResource: staticImage,
  });

  it("selects the requested state", () => {
    expect(selectAvatarAnimation(table, "thinking")).toEqual({
      animation: thinking,
      source: "state",
    });
  });

  it("uses the fallback animation for an unknown state", () => {
    expect(selectAvatarAnimation(table, "sleeping")?.source).toBe("fallback");
    expect(selectAvatarAnimation(table, "sleeping")?.animation).toBe(idle);
  });

  it("uses the static main image when no animation matches", () => {
    const bare = createAvatarStateTable({
      presetStates: ["idle", "thinking"],
      fallbackState: "idle",
      animations: new Map(),
      staticResource: staticImage,
    });

    expect(selectAvatarAnimation(bare, "thinking")).toEqual({
      animation: staticImage,
      source: "static",
    });
  });

  it("returns null when nothing is available", () => {
    const empty = createAvatarStateTable({
      presetStates: [],
      fallbackState: "idle",
      animations: new Map(),
      staticResource: null,
    });

    expect(selectAvatarAnimation(empty, "idle")).toBeNull();
  });
});

describe("createAvatarBehaviorResolver", () => {
  const preset = ["idle", "thinking", "disconnected"];

  it("resolves disconnected before the chat status", () => {
    const resolve = createAvatarBehaviorResolver(builtinAvatarResolvers, preset);

    expect(resolve(context({ connected: false, status: "thinking" }))).toBe("disconnected");
  });

  it("resolves a registered chat status", () => {
    const resolve = createAvatarBehaviorResolver(builtinAvatarResolvers, preset);

    expect(resolve(context({ status: "thinking" }))).toBe("thinking");
    expect(resolve(context({ status: null }))).toBe("disconnected");
  });

  it("ignores statuses that are not registered", () => {
    const resolve = createAvatarBehaviorResolver(builtinAvatarResolvers, ["idle", "thinking"]);

    expect(resolve(context({ status: "sleeping" }))).toBeNull();
  });

  it("obeys resolver priority and allows custom extensions", () => {
    const resolve = createAvatarBehaviorResolver(
      [...builtinAvatarResolvers, (candidate) => (candidate.error ? "executing" : null)],
      [...preset, "executing"],
    );

    expect(resolve({ ...context(), error: true })).toBe("idle");
    expect(resolve({ connected: false, status: "idle", error: true })).toBe("disconnected");
    expect(
      createAvatarBehaviorResolver(
        [(c) => (c.error ? "executing" : null)],
        ["executing"],
      )({ ...context(), error: true }),
    ).toBe("executing");
  });

  it("keeps the current state when nothing resolves", () => {
    expect(createAvatarBehaviorResolver([], preset)(context())).toBeNull();
  });

  it("holds the last resolved state when a later event has no registered key", () => {
    const resolve = createAvatarBehaviorResolver(builtinAvatarResolvers, preset);

    expect(resolve(context({ status: "thinking" }))).toBe("thinking");
    expect(resolve(context({ status: "sleeping" as never }))).toBe("thinking");
  });

  it("requires registered states by default", () => {
    expect(createAvatarBehaviorResolver()(context({ status: "idle" }))).toBeNull();
  });
});
