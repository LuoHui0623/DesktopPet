import { request, type ApiClient } from "./client";
import type {
  ActiveLlmRequest,
  ActiveLlmResponse,
  ChatHistoryQuery,
  ChatHistoryResponse,
  ChatReplyResponse,
  CoreMemoriesResponse,
  DeleteMemoryResponse,
  LlmProvidersResponse,
  MemoriesQuery,
  MemoriesResponse,
  MemoryItem,
  MemoryPatch,
  PersonasResponse,
  ReloadResponse,
  TemporaryModeResponse,
} from "./types";

export interface HealthResponse {
  status: string;
}

export function getHealth(client: ApiClient): Promise<HealthResponse> {
  return request<HealthResponse>(client, "GET", "/api/health");
}

export function sendChat(client: ApiClient, text: string): Promise<ChatReplyResponse> {
  return request<ChatReplyResponse>(client, "POST", "/api/chat", { body: { text } });
}

export function listChatHistory(
  client: ApiClient,
  query: ChatHistoryQuery = {},
): Promise<ChatHistoryResponse> {
  return request<ChatHistoryResponse>(client, "GET", "/api/chat/history", { query });
}

export function listLlmProviders(client: ApiClient): Promise<LlmProvidersResponse> {
  return request<LlmProvidersResponse>(client, "GET", "/api/llm/providers");
}

export function reloadLlmConfig(client: ApiClient): Promise<ReloadResponse> {
  return request<ReloadResponse>(client, "POST", "/api/llm/reload");
}

export function getActiveLlm(client: ApiClient): Promise<ActiveLlmResponse> {
  return request<ActiveLlmResponse>(client, "GET", "/api/llm/active");
}

export function setActiveLlm(
  client: ApiClient,
  selection: ActiveLlmRequest,
): Promise<ActiveLlmResponse> {
  return request<ActiveLlmResponse>(client, "PUT", "/api/llm/active", { body: selection });
}

export function listMemories(
  client: ApiClient,
  query: MemoriesQuery = {},
): Promise<MemoriesResponse> {
  return request<MemoriesResponse>(client, "GET", "/api/memories", { query });
}

export function getMemory(client: ApiClient, memoryId: string): Promise<MemoryItem> {
  return request<MemoryItem>(client, "GET", `/api/memories/${encodeURIComponent(memoryId)}`);
}

export function updateMemory(
  client: ApiClient,
  memoryId: string,
  patch: MemoryPatch,
): Promise<MemoryItem> {
  return request<MemoryItem>(client, "PATCH", `/api/memories/${encodeURIComponent(memoryId)}`, {
    body: patch,
  });
}

export function deleteMemory(client: ApiClient, memoryId: string): Promise<DeleteMemoryResponse> {
  return request<DeleteMemoryResponse>(
    client,
    "DELETE",
    `/api/memories/${encodeURIComponent(memoryId)}`,
  );
}

export function addCoreMemory(client: ApiClient, memoryId: string): Promise<CoreMemoriesResponse> {
  return request<CoreMemoriesResponse>(client, "POST", "/api/memories/core", {
    body: { memory_id: memoryId },
  });
}

export function removeCoreMemory(
  client: ApiClient,
  memoryId: string,
): Promise<CoreMemoriesResponse> {
  return request<CoreMemoriesResponse>(
    client,
    "DELETE",
    `/api/memories/core/${encodeURIComponent(memoryId)}`,
  );
}

export function reorderCoreMemory(
  client: ApiClient,
  memoryIds: string[],
): Promise<CoreMemoriesResponse> {
  return request<CoreMemoriesResponse>(client, "PUT", "/api/memories/core/order", {
    body: { memory_ids: memoryIds },
  });
}

export function listPersonas(client: ApiClient): Promise<PersonasResponse> {
  return request<PersonasResponse>(client, "GET", "/api/personas");
}

export function activatePersona(client: ApiClient, personaId: string): Promise<PersonasResponse> {
  return request<PersonasResponse>(client, "PUT", "/api/personas/active", {
    body: { id: personaId },
  });
}

export function setMemoryTemporaryMode(
  client: ApiClient,
  enabled: boolean,
): Promise<TemporaryModeResponse> {
  return request<TemporaryModeResponse>(client, "POST", "/api/memories/temporary-mode", {
    body: { enabled },
  });
}
