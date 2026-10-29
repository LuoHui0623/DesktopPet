import { createStore } from "zustand/vanilla";
import type { PetState, ServerEvent } from "../../api/types";

export interface ProactiveMessage {
  id: string;
  text: string;
  topic: string;
  at: string;
}

export interface PetStoreState {
  connected: boolean;
  status: PetState | null;
  perceptionCandidate: string | null;
  proactiveMessages: ProactiveMessage[];
  proactiveMuted: boolean;
  applyEvent: (event: ServerEvent) => void;
  setConnected: (connected: boolean) => void;
  setPerceptionCandidate: (exe: string | null) => void;
  dismissProactive: () => void;
  ignoreProactive: () => void;
}

export const PROACTIVE_VISIBLE_MS = 15_000;

export interface PetStoreOptions {
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  visibleMs?: number;
}

export function createPetStore(options: PetStoreOptions = {}) {
  const setTimer =
    options.setTimer ?? ((callback: () => void, ms: number) => globalThis.setTimeout(callback, ms));
  const clearTimer =
    options.clearTimer ?? ((handle: unknown) => globalThis.clearTimeout(handle as number));
  const visibleMs = options.visibleMs ?? PROACTIVE_VISIBLE_MS;
  let bubbleTimer: unknown = null;

  return createStore<PetStoreState>()((set, get) => {
    const stopBubbleTimer = (): void => {
      if (bubbleTimer === null) return;
      clearTimer(bubbleTimer);
      bubbleTimer = null;
    };

    // 只给队首计时；队列推进时重排，保证每条消息各自显示满一个时长。
    const startBubbleTimer = (): void => {
      stopBubbleTimer();
      bubbleTimer = setTimer(() => {
        bubbleTimer = null;
        get().dismissProactive();
      }, visibleMs);
    };

    return {
      connected: false,
      status: null,
      perceptionCandidate: null,
      proactiveMessages: [],
      proactiveMuted: false,
      applyEvent: (event) => {
        if (event.type === "status") {
          set({ status: event.state });
          return;
        }
        if (event.type !== "proactive.message") return;
        if (get().proactiveMuted) return;
        const showing = get().proactiveMessages.length > 0;
        set((state) => ({
          proactiveMessages: [
            ...state.proactiveMessages,
            { id: event.id, text: event.text, topic: event.topic, at: event.at },
          ],
        }));
        if (!showing) startBubbleTimer();
      },
      setConnected: (connected) => {
        // 断开后清空状态：重连后以服务端下一次 status 事件为准，不沿用旧状态。
        set(connected ? { connected: true } : { connected: false, status: null });
      },
      setPerceptionCandidate: (exe) => set({ perceptionCandidate: exe }),
      dismissProactive: () => {
        const messages = get().proactiveMessages;
        if (messages.length === 0) return;
        const remaining = messages.slice(1);
        set({ proactiveMessages: remaining });
        if (remaining.length > 0) startBubbleTimer();
        else stopBubbleTimer();
      },
      ignoreProactive: () => {
        // 忽略只影响本会话（D-46）：清空队列、压住后续事件，不通知后端。
        stopBubbleTimer();
        set({ proactiveMuted: true, proactiveMessages: [] });
      },
    };
  });
}

export const petStore = createPetStore();
