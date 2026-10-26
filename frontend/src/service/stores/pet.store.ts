import { createStore } from "zustand/vanilla";
import type { PetState, ServerEvent } from "../../api/types";

export interface PetStoreState {
  connected: boolean;
  status: PetState | null;
  applyEvent: (event: ServerEvent) => void;
  setConnected: (connected: boolean) => void;
}

export function createPetStore() {
  return createStore<PetStoreState>()((set) => ({
    connected: false,
    status: null,
    applyEvent: (event) => {
      if (event.type === "status") set({ status: event.state });
    },
    setConnected: (connected) => {
      // 断开后清空状态：重连后以服务端下一次 status 事件为准，不沿用旧状态。
      set(connected ? { connected: true } : { connected: false, status: null });
    },
  }));
}

export const petStore = createPetStore();
