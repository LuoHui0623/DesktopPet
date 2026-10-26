import { invoke } from "@tauri-apps/api/core";
import { createApiClient, type BackendInfo } from "../api/client";
import { getHealth } from "../api/endpoints";

export interface BackendHealth {
  port: number;
  status: string;
}

export const GET_BACKEND_INFO_COMMAND = "get_backend_info";

export async function checkBackendHealth(): Promise<BackendHealth> {
  const info = await invoke<BackendInfo>(GET_BACKEND_INFO_COMMAND);
  const client = createApiClient(info);
  const health = await getHealth(client);
  return { port: info.port, status: health.status };
}
