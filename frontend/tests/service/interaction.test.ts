import { describe, expect, it } from "vitest";
import {
  CLIENT_REPORT_INTERVAL_MS,
  createDragTracker,
  createHoverTrigger,
  createInteractionGate,
  createReactionController,
  DRAG_IDLE_MS,
  FALLBACK_INTERACTION_CONFIG,
  HOVER_DWELL_MS,
  REACTION_MS,
} from "../../src/service/interaction.service";

function makeTimers() {
  const pending: { id: number; callback: () => void; ms: number }[] = [];
  let nextId = 1;
  return {
    pending,
    setTimer(callback: () => void, ms: number): number {
      const id = nextId;
      nextId += 1;
      pending.push({ id, callback, ms });
      return id;
    },
    clearTimer(handle: unknown): void {
      const index = pending.findIndex((timer) => timer.id === handle);
      if (index >= 0) pending.splice(index, 1);
    },
    fire(): number | undefined {
      const next = pending.shift();
      next?.callback();
      return next?.ms;
    },
  };
}

describe("interaction gate", () => {
  it("allows one report per kind within the configured interval", () => {
    let now = 0;
    const allow = createInteractionGate(FALLBACK_INTERACTION_CONFIG, () => now);

    expect(allow("click")).toBe(true);
    now += CLIENT_REPORT_INTERVAL_MS - 1;
    expect(allow("click")).toBe(false);
    now += 1;
    expect(allow("click")).toBe(true);
  });

  it("throttles each kind separately", () => {
    const allow = createInteractionGate(FALLBACK_INTERACTION_CONFIG, () => 0);

    expect(allow("click")).toBe(true);
    expect(allow("hover")).toBe(true);
    expect(allow("drag")).toBe(true);
    expect(allow("click")).toBe(false);
  });

  it("follows the backend config instead of the bundled defaults", () => {
    let now = 0;
    const allow = createInteractionGate(
      { ...FALLBACK_INTERACTION_CONFIG, report_interval_ms: 5000 },
      () => now,
    );

    expect(allow("click")).toBe(true);
    now += 4_999;
    expect(allow("click")).toBe(false);
    now += 1;
    expect(allow("click")).toBe(true);
  });

  it("drops kinds the user switched off", () => {
    const allow = createInteractionGate(
      { ...FALLBACK_INTERACTION_CONFIG, report_enabled_kinds: ["click"] },
      () => 0,
    );

    expect(allow("click")).toBe(true);
    expect(allow("hover")).toBe(false);
    expect(allow("drag")).toBe(false);
    expect(allow("menu")).toBe(false);
  });

  it("reports nothing when interval throttling is disabled", () => {
    let now = 0;
    const allow = createInteractionGate(
      { ...FALLBACK_INTERACTION_CONFIG, report_interval_ms: 0 },
      () => now,
    );

    expect(allow("click")).toBe(true);
    expect(allow("click")).toBe(true);
    now += 1;
    expect(allow("click")).toBe(true);
  });
});

describe("reaction controller", () => {
  it("shows the reaction and clears it after the duration", () => {
    const timers = makeTimers();
    const seen: (string | null)[] = [];
    const reaction = createReactionController((kind) => seen.push(kind), timers);

    reaction.show("click");
    expect(seen).toEqual(["click"]);
    expect(timers.pending.map((timer) => timer.ms)).toEqual([REACTION_MS]);

    timers.fire();
    expect(seen).toEqual(["click", null]);
  });

  it("restarts the timer when another reaction arrives", () => {
    const timers = makeTimers();
    const seen: (string | null)[] = [];
    const reaction = createReactionController((kind) => seen.push(kind), timers);

    reaction.show("click");
    reaction.show("hover");

    expect(seen).toEqual(["click", "hover"]);
    expect(timers.pending).toHaveLength(1);
  });

  it("stops without emitting another change", () => {
    const timers = makeTimers();
    const seen: (string | null)[] = [];
    const reaction = createReactionController((kind) => seen.push(kind), timers);

    reaction.show("drag");
    reaction.stop();

    expect(timers.pending).toEqual([]);
    expect(seen).toEqual(["drag"]);
  });
});

describe("hover trigger", () => {
  it("reports only after the dwell time", () => {
    const timers = makeTimers();
    const hits: number[] = [];
    const hover = createHoverTrigger(() => hits.push(1), timers);

    hover.start();
    expect(hits).toEqual([]);
    expect(timers.pending.map((timer) => timer.ms)).toEqual([HOVER_DWELL_MS]);

    timers.fire();
    expect(hits).toEqual([1]);
  });

  it("cancels when the pointer leaves early", () => {
    const timers = makeTimers();
    const hits: number[] = [];
    const hover = createHoverTrigger(() => hits.push(1), timers);

    hover.start();
    hover.stop();
    hover.start();
    hover.stop();

    expect(timers.pending).toEqual([]);
    expect(hits).toEqual([]);
  });

  it("does not schedule twice while the pointer stays inside", () => {
    const timers = makeTimers();
    const hover = createHoverTrigger(() => undefined, timers);

    hover.start();
    hover.start();

    expect(timers.pending).toHaveLength(1);
  });
});

describe("drag tracker", () => {
  it("reports once after movement stops", () => {
    const timers = makeTimers();
    const hits: number[] = [];
    const drag = createDragTracker(() => hits.push(1), timers);

    drag.start();
    drag.move();
    drag.move();
    expect(timers.pending.map((timer) => timer.ms)).toEqual([DRAG_IDLE_MS]);

    timers.fire();
    drag.end();

    expect(hits).toEqual([1]);
  });

  it("reports immediately when the drag ends before the idle timer", () => {
    const timers = makeTimers();
    const hits: number[] = [];
    const drag = createDragTracker(() => hits.push(1), timers);

    drag.start();
    drag.move();
    drag.end();

    expect(hits).toEqual([1]);
    expect(timers.pending).toEqual([]);
  });

  it("does not report clicks without movement", () => {
    const timers = makeTimers();
    const hits: number[] = [];
    const drag = createDragTracker(() => hits.push(1), timers);

    drag.start();
    drag.end();
    drag.move();

    expect(hits).toEqual([]);
    expect(timers.pending).toEqual([]);
  });

  it("ignores movement before the pointer is armed", () => {
    const timers = makeTimers();
    const hits: number[] = [];
    const drag = createDragTracker(() => hits.push(1), timers);

    drag.move();

    expect(timers.pending).toEqual([]);
    expect(hits).toEqual([]);
  });
});
