import { describe, expect, it, vi } from "vitest";
import { createPetHitState } from "../../src/modules/pet/pet-hit-state";

describe("createPetHitState", () => {
  it("tracks hover while unlocked", () => {
    const isHit = vi.fn((x: number) => x > 10);
    const state = createPetHitState(isHit);

    expect(state.move(5, 0)).toBe(false);
    expect(state.isHovered()).toBe(false);
    expect(state.move(11, 0)).toBe(true);
    expect(state.isHovered()).toBe(true);
    expect(state.move(1, 0)).toBe(false);
    expect(state.isHovered()).toBe(false);
  });

  it("locks on a hit press and skips further mask lookups", () => {
    const isHit = vi.fn(() => true);
    const state = createPetHitState(isHit);

    expect(state.down(10, 10)).toBe(true);
    expect(state.isLocked()).toBe(true);
    isHit.mockClear();

    expect(state.move(0, 0)).toBe(true);
    expect(state.enter(0, 0)).toBe(true);
    state.leave();
    expect(isHit).not.toHaveBeenCalled();
    expect(state.isLocked()).toBe(true);
    expect(state.isHovered()).toBe(true);
  });

  it("does not lock on a transparent press", () => {
    const state = createPetHitState(() => false);

    expect(state.down(0, 0)).toBe(false);
    expect(state.isLocked()).toBe(false);
  });

  it("unlocks on pointerup and re-evaluates afterwards", () => {
    let hit = true;
    const state = createPetHitState(() => hit);

    state.down(0, 0);
    state.up();
    expect(state.isLocked()).toBe(false);

    hit = false;
    expect(state.move(0, 0)).toBe(false);
    expect(state.isHovered()).toBe(false);
  });

  it("ignores leave while locked so drag keeps its hover state", () => {
    const state = createPetHitState(() => true);

    state.down(0, 0);
    state.leave();

    expect(state.isHovered()).toBe(true);
  });

  it("does not re-query on enter while already hovered", () => {
    const isHit = vi.fn(() => true);
    const state = createPetHitState(isHit);

    state.enter(1, 1);
    state.enter(2, 2);

    expect(isHit).toHaveBeenCalledTimes(1);
  });
});
