import { describe, expect, it, vi } from "vitest";
import {
  PROACTIVE_VISIBLE_MS,
  createPetStore,
  type PetStoreOptions,
} from "../../src/service/stores/pet.store";

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

function setup(options: Omit<PetStoreOptions, "setTimer" | "clearTimer"> = {}) {
  const timers = makeTimers();
  const store = createPetStore({
    ...options,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  return { store, timers };
}

function proactive(id: string, text = "起来动动吧。") {
  return {
    type: "proactive.message" as const,
    id,
    text,
    topic: "app_in_use.code",
    at: "2026-10-29T10:00:00+08:00",
  };
}

describe("pet store: proactive messages", () => {
  it("shows a proactive message and schedules it to disappear", () => {
    const { store, timers } = setup();
    store.getState().applyEvent(proactive("p1"));

    expect(store.getState().proactiveMessages.map((message) => message.id)).toEqual(["p1"]);
    expect(timers.pending.map((timer) => timer.ms)).toEqual([PROACTIVE_VISIBLE_MS]);

    timers.fire();
    expect(store.getState().proactiveMessages).toEqual([]);
    expect(timers.pending).toEqual([]);
  });

  it("queues messages and shows them one at a time", () => {
    const { store, timers } = setup();
    store.getState().applyEvent(proactive("p1", "第一句"));
    store.getState().applyEvent(proactive("p2", "第二句"));

    expect(store.getState().proactiveMessages.map((message) => message.text)).toEqual([
      "第一句",
      "第二句",
    ]);
    expect(timers.pending).toHaveLength(1);

    timers.fire();
    expect(store.getState().proactiveMessages.map((message) => message.id)).toEqual(["p2"]);
    expect(timers.pending.map((timer) => timer.ms)).toEqual([PROACTIVE_VISIBLE_MS]);

    timers.fire();
    expect(store.getState().proactiveMessages).toEqual([]);
  });

  it("keeps the current message visible for its full duration when another arrives", () => {
    const { store, timers } = setup({ visibleMs: 1000 });
    store.getState().applyEvent(proactive("p1"));
    const firstTimer = timers.pending[0];
    store.getState().applyEvent(proactive("p2"));
    expect(timers.pending[0]).toBe(firstTimer);
  });

  it("advances the queue when the bubble is clicked and replaces the timer", () => {
    const { store, timers } = setup();
    store.getState().applyEvent(proactive("p1"));
    store.getState().applyEvent(proactive("p2"));

    store.getState().dismissProactive();
    expect(store.getState().proactiveMessages.map((message) => message.id)).toEqual(["p2"]);
    expect(timers.pending).toHaveLength(1);
  });

  it("clears and mutes for the session when ignored", () => {
    const { store, timers } = setup();
    store.getState().applyEvent(proactive("p1"));
    store.getState().applyEvent(proactive("p2"));

    store.getState().ignoreProactive();
    expect(store.getState().proactiveMessages).toEqual([]);
    expect(store.getState().proactiveMuted).toBe(true);
    expect(timers.pending).toEqual([]);

    store.getState().applyEvent(proactive("p3"));
    expect(store.getState().proactiveMessages).toEqual([]);
    expect(timers.pending).toEqual([]);
  });

  it("ignores unrelated events and keeps the pet status working", () => {
    const { store } = setup();
    store.getState().applyEvent({ type: "status", state: "thinking" });
    store.getState().applyEvent({ type: "chat.done", message_id: "m1", text: "你好", sources: [] });
    expect(store.getState().status).toBe("thinking");
    expect(store.getState().proactiveMessages).toEqual([]);
  });

  it("does not call the backend when a message is ignored", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { store } = setup();
    store.getState().applyEvent(proactive("p1"));
    store.getState().ignoreProactive();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });
});

function proactiveTurn(id: string, at: string, texts: string[]) {
  return {
    type: "proactive.message" as const,
    id,
    text: texts.join("\n"),
    topic: "app_in_use.code",
    at,
    messages: texts.map((text, index) => ({
      message_id: `${id}_${index + 1}`,
      text,
      at,
      sources: [],
    })),
  };
}

describe("pet store: several utterances in one proactive turn", () => {
  it("queues every utterance and shows them one at a time", () => {
    const { store, timers } = setup();
    store
      .getState()
      .applyEvent(proactiveTurn("p1", "2026-10-29T10:00:07+08:00", ["第一句", "第二句"]));

    expect(store.getState().proactiveMessages.map((message) => message.text)).toEqual([
      "第一句",
      "第二句",
    ]);
    expect(store.getState().proactiveMessages.map((message) => message.id)).toEqual([
      "p1_1",
      "p1_2",
    ]);
    expect(timers.pending.map((timer) => timer.ms)).toEqual([PROACTIVE_VISIBLE_MS]);

    timers.fire();
    expect(store.getState().proactiveMessages.map((message) => message.text)).toEqual(["第二句"]);
    expect(timers.pending.map((timer) => timer.ms)).toEqual([PROACTIVE_VISIBLE_MS]);
  });

  it("keeps each utterance's own time", () => {
    const { store } = setup();
    store
      .getState()
      .applyEvent(proactiveTurn("p1", "2026-10-29T10:00:07+08:00", ["第一句", "第二句"]));
    expect(store.getState().proactiveMessages.map((message) => message.at)).toEqual([
      "2026-10-29T10:00:07+08:00",
      "2026-10-29T10:00:07+08:00",
    ]);
  });

  it("shows an event without messages as one bubble", () => {
    const { store, timers } = setup();
    store.getState().applyEvent(proactive("p1", "起来动动吧。"));
    expect(store.getState().proactiveMessages).toEqual([
      {
        id: "p1",
        text: "起来动动吧。",
        topic: "app_in_use.code",
        at: "2026-10-29T10:00:00+08:00",
      },
    ]);
    expect(timers.pending).toHaveLength(1);
  });

  it("mutes the whole turn when ignored in the middle of it", () => {
    const { store, timers } = setup();
    store
      .getState()
      .applyEvent(proactiveTurn("p1", "2026-10-29T10:00:07+08:00", ["第一句", "第二句"]));
    store.getState().ignoreProactive();
    expect(store.getState().proactiveMessages).toEqual([]);
    expect(timers.pending).toEqual([]);
  });
});
