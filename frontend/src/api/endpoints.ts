import { request, type ApiClient } from "./client";

export interface HealthResponse {
  status: string;
}

export function getHealth(client: ApiClient): Promise<HealthResponse> {
  return request<HealthResponse>(client, "GET", "/api/health");
}
