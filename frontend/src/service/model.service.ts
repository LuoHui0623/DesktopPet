import { getActiveLlm, listLlmProviders, reloadLlmConfig, setActiveLlm } from "../api/endpoints";
import type {
  ActiveLlmRequest,
  ActiveLlmResponse,
  ActiveSelection,
  LlmProvidersResponse,
  ReloadResponse,
} from "../api/types";
import { getApiClient } from "./api-client";

export type ModelCapability = "chat" | "consolidate";

export interface ModelSettingsSnapshot {
  providers: LlmProvidersResponse;
  active: ActiveLlmResponse;
}

export async function loadModelSettings(): Promise<ModelSettingsSnapshot> {
  const client = await getApiClient();
  const [providers, active] = await Promise.all([listLlmProviders(client), getActiveLlm(client)]);
  return { providers, active };
}

export async function switchActiveModel(
  capability: ModelCapability,
  selection: ActiveSelection,
): Promise<ActiveLlmResponse> {
  const client = await getApiClient();
  const body: ActiveLlmRequest =
    capability === "chat" ? { chat: selection } : { consolidate: selection };
  return setActiveLlm(client, body);
}

export async function reloadModelConfig(): Promise<ReloadResponse> {
  const client = await getApiClient();
  return reloadLlmConfig(client);
}
