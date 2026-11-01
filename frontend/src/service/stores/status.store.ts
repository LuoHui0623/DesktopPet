import { createStore } from "zustand/vanilla";
import type { ServerEvent, UsageCall, UsagePanelMode, UsagePanelTurn } from "../../api/types";

export interface StatusStoreState {
  connected: boolean;
  mode: UsagePanelMode | null;
  turn: UsagePanelTurn | null;
  applyEvent: (event: ServerEvent) => void;
  setConnected: (connected: boolean) => void;
}

function emptyTurn(turnId: string, startedAt: string): UsagePanelTurn {
  return {
    turn_id: turnId,
    started_at: startedAt,
    finished_at: null,
    finished: false,
    tokens_in: null,
    tokens_out: null,
    tokens_complete: false,
    model_time_ms: 0,
    calls: [],
  };
}

// 迟到或重复的事件只补空字段：已经拿到 token 或回复后，不因更早的事件回退。
function mergeCall(current: UsageCall, incoming: UsageCall): UsageCall {
  return {
    provider: incoming.provider ?? current.provider,
    model: incoming.model ?? current.model,
    purpose: incoming.purpose ?? current.purpose,
    turn_id: incoming.turn_id ?? current.turn_id,
    run_id: incoming.run_id ?? current.run_id,
    prompt_hash: incoming.prompt_hash ?? current.prompt_hash,
    started_at: incoming.started_at || current.started_at,
    finished_at: incoming.finished_at ?? current.finished_at,
    tokens_in: incoming.tokens_in ?? current.tokens_in,
    tokens_out: incoming.tokens_out ?? current.tokens_out,
    usage_raw: incoming.usage_raw ?? current.usage_raw,
    latency_ms: incoming.latency_ms ?? current.latency_ms,
    success: incoming.success ?? current.success,
    prompt_text: incoming.prompt_text ?? current.prompt_text,
    reply_text: incoming.reply_text ?? current.reply_text,
    reply_raw: incoming.reply_raw ?? current.reply_raw,
  };
}

function upsertCall(calls: UsageCall[], incoming: UsageCall): UsageCall[] {
  const index = calls.findIndex(
    (call) => call.purpose === incoming.purpose && call.started_at === incoming.started_at,
  );
  if (index === -1) return [...calls, incoming];
  return calls.map((call, position) => (position === index ? mergeCall(call, incoming) : call));
}

function isCurrent(turn: UsagePanelTurn | null, turnId: string | null): turn is UsagePanelTurn {
  return turn !== null && !turn.finished && turnId === turn.turn_id;
}

export function createStatusStore() {
  return createStore<StatusStoreState>()((set, get) => ({
    connected: false,
    mode: null,
    turn: null,
    applyEvent: (event) => {
      if (event.type === "usage.panel.snapshot") {
        set({ mode: event.mode, turn: event.turn });
        return;
      }
      if (event.type === "usage.turn.started") {
        const current = get().turn;
        if (current?.turn_id === event.turn_id) return;
        set({ turn: emptyTurn(event.turn_id, event.at) });
        return;
      }
      if (event.type === "usage.call.started") {
        set((state) => {
          if (!isCurrent(state.turn, event.call.turn_id)) return {};
          return { turn: { ...state.turn, calls: upsertCall(state.turn.calls, event.call) } };
        });
        return;
      }
      if (event.type === "usage.call.finished") {
        set((state) => {
          if (!isCurrent(state.turn, event.call.turn_id)) return {};
          return { turn: { ...state.turn, calls: upsertCall(state.turn.calls, event.call) } };
        });
        return;
      }
      if (event.type === "usage.turn.finished") {
        set((state) => {
          const turn = state.turn;
          if (turn === null || turn.turn_id !== event.turn_id) return {};
          return {
            turn: {
              ...turn,
              finished: true,
              started_at: event.started_at ?? turn.started_at,
              finished_at: event.finished_at,
              tokens_in: event.tokens_in,
              tokens_out: event.tokens_out,
              tokens_complete: event.tokens_complete,
              model_time_ms: event.model_time_ms,
              calls: event.calls,
            },
          };
        });
      }
    },
    setConnected: (connected) => {
      // 断开即清空：重连后先收到服务端快照，不沿用断线前的旧轮。
      set(connected ? { connected: true } : { connected: false, mode: null, turn: null });
    },
  }));
}

export const statusStore = createStatusStore();
