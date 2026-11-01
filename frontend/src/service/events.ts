import { invoke } from "@tauri-apps/api/core";
import { createApiClient, type BackendInfo } from "../api/client";
import { connectEvents } from "../api/ws";
import type { ServerEvent } from "../api/types";
import { GET_BACKEND_INFO_COMMAND } from "./backend.service";
import { chatStore, type ChatStoreState } from "./stores/chat.store";
import { petStore, type PetStoreState } from "./stores/pet.store";
import { statusStore, type StatusStoreState } from "./stores/status.store";

export type PetStore = { getState: () => PetStoreState };
export type ChatStore = { getState: () => ChatStoreState };
export type StatusStore = { getState: () => StatusStoreState };

interface EventHandlers {
  onEvent: (event: ServerEvent) => void;
  onConnectionChange?: (connected: boolean) => void;
}

async function startEvents(handlers: EventHandlers): Promise<() => void> {
  const info = await invoke<BackendInfo>(GET_BACKEND_INFO_COMMAND);
  const stream = connectEvents({
    client: createApiClient(info),
    onEvent: handlers.onEvent,
    onConnectionChange: handlers.onConnectionChange,
  });
  return () => stream.close();
}

export function startPetEvents(store: PetStore = petStore): Promise<() => void> {
  return startEvents({
    onEvent: (event) => store.getState().applyEvent(event),
    onConnectionChange: (connected) => store.getState().setConnected(connected),
  });
}

export function startChatEvents(store: ChatStore = chatStore): Promise<() => void> {
  return startEvents({
    onEvent: (event) => store.getState().applyEvent(event),
  });
}

export function startStatusEvents(store: StatusStore = statusStore): Promise<() => void> {
  return startEvents({
    onEvent: (event) => store.getState().applyEvent(event),
    onConnectionChange: (connected) => store.getState().setConnected(connected),
  });
}
