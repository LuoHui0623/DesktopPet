import { reloadLlmConfig, updateSettingsModel } from "../api/endpoints";
import type { ActiveLlmRequest, ActiveSelection, ReloadResponse, SettingsView } from "../api/types";
import { getApiClient } from "./api-client";

export type ModelCapability = "chat" | "consolidate";

export async function switchActiveModel(
  capability: ModelCapability,
  selection: ActiveSelection,
  version: number,
): Promise<SettingsView> {
  const client = await getApiClient();
  const body: ActiveLlmRequest =
    capability === "chat" ? { chat: selection } : { consolidate: selection };
  return updateSettingsModel(client, body, version);
}

export async function reloadModelConfig(): Promise<ReloadResponse> {
  const client = await getApiClient();
  return reloadLlmConfig(client);
}
