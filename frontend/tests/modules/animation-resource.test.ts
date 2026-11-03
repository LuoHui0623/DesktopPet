import { describe, expect, it } from "vitest";
import {
  DEFAULT_FRAME_DELAY_MS,
  MIN_FRAME_DELAY_MS,
  animationDuration,
  buildGifAnimation,
  buildSequenceAnimation,
  buildStaticAnimation,
  frameAt,
  frameIndexAt,
  normalizeFrameDelay,
  resourceAnchor,
  resourceFrameId,
  timeToNextFrame,
  type RawAnimationFrame,
} from "../../src/modules/pet/animation-resource";

const canvas = { width: 240, height: 240 };

function frame(durationMs: number, source?: string): RawAnimationFrame {
  return { image: null, durationMs, mask: null, source };
}

describe("normalizeFrameDelay", () => {
  it("uses the default delay for zero and invalid durations", () => {
    expect(normalizeFrameDelay(0)).toBe(DEFAULT_FRAME_DELAY_MS);
    expect(normalizeFrameDelay(Number.NaN)).toBe(DEFAULT_FRAME_DELAY_MS);
    expect(normalizeFrameDelay(-5)).toBe(DEFAULT_FRAME_DELAY_MS);
  });

  it("clamps short delays to the minimum", () => {
    expect(normalizeFrameDelay(10)).toBe(MIN_FRAME_DELAY_MS);
    expect(normalizeFrameDelay(19)).toBe(MIN_FRAME_DELAY_MS);
  });

  it("keeps delays at or above the minimum", () => {
    expect(normalizeFrameDelay(20)).toBe(20);
    expect(normalizeFrameDelay(160)).toBe(160);
  });
});

describe("resourceFrameId", () => {
  it("combines the pack, the source and the frame index", () => {
    expect(resourceFrameId("default", "frames/idle_0.png", 3)).toBe("default/frames/idle_0.png#3");
  });
});

describe("buildSequenceAnimation", () => {
  it("keeps one frame per file and stamps the source id", () => {
    const animation = buildSequenceAnimation("default", "frames/thinking", canvas, [
      frame(160, "frames/thinking_0.png"),
      frame(160, "frames/thinking_1.png"),
    ]);

    expect(animation.kind).toBe("sequence");
    expect(animation.frames.map((entry) => entry.id)).toEqual([
      "default/frames/thinking_0.png#0",
      "default/frames/thinking_1.png#1",
    ]);
    expect(animation.frames.map((entry) => entry.source)).toEqual([
      "frames/thinking_0.png",
      "frames/thinking_1.png",
    ]);
  });

  it("falls back to the resource source when a frame omits it", () => {
    const animation = buildSequenceAnimation("default", "idle.png", canvas, [frame(500)]);

    expect(animation.frames[0].source).toBe("idle.png");
    expect(animation.frames[0].id).toBe("default/idle.png#0");
  });
});

describe("buildGifAnimation", () => {
  it("uses the decoded per-frame delays", () => {
    const animation = buildGifAnimation("default", "frames/idle.gif", canvas, [
      frame(120),
      frame(10),
    ]);

    expect(animation.kind).toBe("gif");
    expect(animation.frames.map((entry) => entry.durationMs)).toEqual([120, 20]);
  });
});

describe("buildStaticAnimation", () => {
  it("builds a single-frame sequence", () => {
    const animation = buildStaticAnimation("default", "static.png", canvas, frame(0));

    expect(animation.kind).toBe("sequence");
    expect(animation.frames).toHaveLength(1);
    expect(animation.frames[0].durationMs).toBe(DEFAULT_FRAME_DELAY_MS);
  });
});

describe("timeline", () => {
  const animation = buildSequenceAnimation("default", "idle", canvas, [
    frame(100, "a.png"),
    frame(50, "b.png"),
    frame(50, "c.png"),
  ]);

  it("sums the frame durations", () => {
    expect(animationDuration(animation)).toBe(200);
  });

  it("selects frames by cumulative time and loops", () => {
    expect(frameIndexAt(animation, 0)).toBe(0);
    expect(frameIndexAt(animation, 99)).toBe(0);
    expect(frameIndexAt(animation, 100)).toBe(1);
    expect(frameIndexAt(animation, 150)).toBe(2);
    expect(frameIndexAt(animation, 200)).toBe(0);
    expect(frameIndexAt(animation, 310)).toBe(1);
  });

  it("reports the remaining time to the next frame", () => {
    expect(timeToNextFrame(animation, 0)).toBe(100);
    expect(timeToNextFrame(animation, 60)).toBe(40);
    expect(timeToNextFrame(animation, 100)).toBe(50);
    expect(timeToNextFrame(animation, 175)).toBe(25);
  });

  it("returns a default delay for an empty timeline", () => {
    const empty = buildSequenceAnimation("default", "idle", canvas, []);

    expect(frameIndexAt(empty, 10)).toBe(-1);
    expect(frameAt(empty, 10)).toBeNull();
    expect(timeToNextFrame(empty, 10)).toBe(DEFAULT_FRAME_DELAY_MS);
  });

  it("returns the current frame object", () => {
    expect(frameAt(animation, 120)?.source).toBe("b.png");
  });
});

describe("resourceAnchor", () => {
  it("uses the resource rectangle centre", () => {
    expect(resourceAnchor(canvas)).toEqual({ x: 120, y: 120 });
  });
});
