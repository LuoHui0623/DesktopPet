import { invoke } from "@tauri-apps/api/core";
import { createApiClient, type BackendInfo } from "../api/client";
import { connectEvents } from "../api/ws";
import { GET_BACKEND_INFO_COMMAND } from "./backend.service";
import { petStore, type PetStoreState } from "./stores/pet.store";

export type PetStore = { getState: () => PetStoreState };

export async function startPetEvents(store: PetStore = petStore): Promise<() => void> {
  const info = await invoke<BackendInfo>(GET_BACKEND_INFO_COMMAND);
  const stream = connectEvents({
    client: createApiClient(info),
    onEvent: (event) => store.getState().applyEvent(event),
    onConnectionChange: (connected) => store.getState().setConnected(connected),
  });
  return () => stream.close();
}
