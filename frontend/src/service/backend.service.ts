import { createApiClient } from "../api/client";
import { getHealth } from "../api/endpoints";

export interface BackendHealth {
  port: number;
  status: string;
}

export async function checkBackendHealth(): Promise<BackendHealth> {
  const info = await window.petBackend.getInfo();
  const client = createApiClient(info);
  const health = await getHealth(client);
  return { port: info.port, status: health.status };
}
