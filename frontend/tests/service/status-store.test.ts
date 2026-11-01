import { describe, expect, it } from "vitest";
import type { ServerEvent, UsageCall, UsagePanelTurn } from "../../src/api/types";
import { createStatusStore } from "../../src/service/stores/status.store";

const START = "2026-11-01T10:00:00.000+08:00";
const NEXT = "2026-11-01T10:00:01.000+08:00";
const END = "2026-11-01T10:00:01.200+08:00";

function call(overrides: Partial<UsageCall> = {}): UsageCall {
  return {
    provider: "deepseek",
    model: "deepseek-flash",
    purpose: "chat",
    turn_id: "t1",
    run_id: null,
    prompt_hash: "h1",
    started_at: START,
    finished_at: null,
    tokens_in: null,
    tokens_out: null,
    usage_raw: null,
    latency_ms: null,
    success: null,
    prompt_text: "[human]\n你好",
    reply_text: null,
    reply_raw: null,
    ...overrides,
  };
}

function turn(overrides: Partial<UsagePanelTurn> = {}): UsagePanelTurn {
  return {
    turn_id: "t1",
    started_at: START,
    finished_at: null,
    finished: false,
    tokens_in: null,
    tokens_out: null,
    tokens_complete: false,
    model_time_ms: 0,
    calls: [],
    ...overrides,
  };
}

function started(overrides: Partial<UsageCall> = {}): ServerEvent {
  return { type: "usage.call.started", call: call(overrides) };
}

function finished(overrides: Partial<UsageCall> = {}): ServerEvent {
  return {
    type: "usage.call.finished",
    call: call({
      finished_at: END,
      tokens_in: 3,
      tokens_out: 5,
      latency_ms: 1200,
      success: true,
      reply_text: "好的",
      ...overrides,
    }),
  };
}

function begin(store: ReturnType<typeof createStatusStore>, turnId = "t1", at = START): void {
  store.getState().applyEvent({ type: "usage.turn.started", turn_id: turnId, at });
}

describe("status store", () => {
  it("starts empty and disconnected", () => {
    const store = createStatusStore();
    const state = store.getState();

    expect(state.connected).toBe(false);
    expect(state.mode).toBeNull();
    expect(state.turn).toBeNull();
  });

  it("takes the mode and current turn from the connection snapshot", () => {
    const store = createStatusStore();
    store.getState().applyEvent({
      type: "usage.panel.snapshot",
      mode: "dev",
      turn: turn({ calls: [call({ reply_text: "好的", finished_at: END })] }),
    });

    expect(store.getState().mode).toBe("dev");
    expect(store.getState().turn?.turn_id).toBe("t1");
    expect(store.getState().turn?.calls).toHaveLength(1);
  });

  it("creates a turn when a round starts", () => {
    const store = createStatusStore();
    begin(store);

    expect(store.getState().turn).toEqual({
      turn_id: "t1",
      started_at: START,
      finished_at: null,
      finished: false,
      tokens_in: null,
      tokens_out: null,
      tokens_complete: false,
      model_time_ms: 0,
      calls: [],
    });
  });

  it("replaces the calls when the next round starts", () => {
    const store = createStatusStore();
    begin(store);
    store.getState().applyEvent(started());
    begin(store, "t2", NEXT);

    const current = store.getState().turn;
    expect(current?.turn_id).toBe("t2");
    expect(current?.calls).toEqual([]);
  });

  it("keeps existing calls when the same round start is repeated", () => {
    const store = createStatusStore();
    begin(store);
    store.getState().applyEvent(started());
    begin(store);

    expect(store.getState().turn?.calls).toHaveLength(1);
  });

  it("merges started and finished events into one call in arrival order", () => {
    const store = createStatusStore();
    begin(store);
    store.getState().applyEvent(started());
    store.getState().applyEvent(started({ purpose: "consolidate", started_at: NEXT }));
    store.getState().applyEvent(finished());

    const calls = store.getState().turn?.calls ?? [];
    expect(calls.map((item) => item.purpose)).toEqual(["chat", "consolidate"]);
    expect(calls[0]?.finished_at).toBe(END);
    expect(calls[0]?.tokens_in).toBe(3);
    expect(calls[0]?.reply_text).toBe("好的");
    expect(calls[1]?.finished_at).toBeNull();
  });

  it("records a finished call that never announced its start", () => {
    const store = createStatusStore();
    begin(store);
    store.getState().applyEvent(finished());

    const calls = store.getState().turn?.calls ?? [];
    expect(calls).toHaveLength(1);
    expect(calls[0]?.reply_text).toBe("好的");
  });

  it("keeps the raw reply when a late start event arrives", () => {
    const store = createStatusStore();
    begin(store);
    store.getState().applyEvent(finished({ reply_raw: '{"type": "chat", "text": ["好的"]}' }));
    store.getState().applyEvent(started());

    const calls = store.getState().turn?.calls ?? [];
    expect(calls).toHaveLength(1);
    expect(calls[0]?.reply_raw).toBe('{"type": "chat", "text": ["好的"]}');
  });

  it("ignores calls from another round or without a round", () => {
    const store = createStatusStore();
    begin(store, "t2");
    store.getState().applyEvent(started({ turn_id: "t1" }));
    store.getState().applyEvent(finished({ turn_id: "t1" }));
    store.getState().applyEvent(started({ turn_id: null }));

    expect(store.getState().turn?.calls).toEqual([]);
  });

  it("ignores calls that arrive after the round finished", () => {
    const store = createStatusStore();
    begin(store);
    store.getState().applyEvent({
      type: "usage.turn.finished",
      turn_id: "t1",
      tokens_in: 3,
      tokens_out: 5,
      tokens_complete: true,
      model_time_ms: 1200,
      started_at: START,
      finished_at: END,
      calls: [call({ finished_at: END, tokens_in: 3, tokens_out: 5, reply_text: "好的" })],
    });
    store.getState().applyEvent(finished({ reply_text: "迟到" }));

    const calls = store.getState().turn?.calls ?? [];
    expect(calls).toHaveLength(1);
    expect(calls[0]?.reply_text).toBe("好的");
  });

  it("uses the round aggregate and persisted calls as authoritative", () => {
    const store = createStatusStore();
    begin(store);
    store.getState().applyEvent(started());
    store.getState().applyEvent(finished());
    store.getState().applyEvent({
      type: "usage.turn.finished",
      turn_id: "t1",
      tokens_in: 8,
      tokens_out: 13,
      tokens_complete: true,
      model_time_ms: 2400,
      started_at: START,
      finished_at: END,
      calls: [
        call({ finished_at: END, tokens_in: 3, tokens_out: 5, reply_text: "好的" }),
        call({
          purpose: "consolidate",
          started_at: NEXT,
          finished_at: END,
          tokens_in: 5,
          tokens_out: 8,
          reply_text: "整理完成",
        }),
      ],
    });

    const current = store.getState().turn;
    expect(current?.finished).toBe(true);
    expect(current?.tokens_in).toBe(8);
    expect(current?.tokens_out).toBe(13);
    expect(current?.model_time_ms).toBe(2400);
    expect(current?.calls.map((item) => item.purpose)).toEqual(["chat", "consolidate"]);
  });

  it("keeps unknown tokens unknown and reports an incomplete round", () => {
    const store = createStatusStore();
    begin(store);
    store.getState().applyEvent(finished({ tokens_in: null, tokens_out: null }));
    store.getState().applyEvent({
      type: "usage.turn.finished",
      turn_id: "t1",
      tokens_in: null,
      tokens_out: null,
      tokens_complete: false,
      model_time_ms: 1200,
      started_at: START,
      finished_at: END,
      calls: [call({ finished_at: END, tokens_in: null, tokens_out: null })],
    });

    const current = store.getState().turn;
    expect(current?.tokens_in).toBeNull();
    expect(current?.tokens_out).toBeNull();
    expect(current?.tokens_complete).toBe(false);
    expect(current?.calls[0]?.tokens_in).toBeNull();
  });

  it("drops the panel on disconnect so a reconnect cannot show a stale round", () => {
    const store = createStatusStore();
    begin(store);
    store.getState().applyEvent(started());
    store.getState().setConnected(true);

    store.getState().setConnected(false);

    expect(store.getState().turn).toBeNull();
    expect(store.getState().mode).toBeNull();
  });

  it("restores the current round from the reconnect snapshot", () => {
    const store = createStatusStore();
    store.getState().setConnected(false);
    store.getState().applyEvent({
      type: "usage.panel.snapshot",
      mode: "prod",
      turn: turn({ calls: [call({ finished_at: END, tokens_in: 3, tokens_out: 5 })] }),
    });
    store.getState().setConnected(true);

    expect(store.getState().connected).toBe(true);
    expect(store.getState().turn?.turn_id).toBe("t1");
    expect(store.getState().turn?.calls).toHaveLength(1);
  });
});
