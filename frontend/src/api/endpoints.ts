import { request, type ApiClient } from "./client";
import type {
  ActiveLlmRequest,
  ActiveLlmResponse,
  ApprovalAck,
  ApprovalDecision,
  ChatHistoryQuery,
  ChatHistoryResponse,
  ChatReplyResponse,
  LlmProvidersResponse,
  MemoriesQuery,
  MemoriesResponse,
  PersonasResponse,
  ReloadResponse,
  StructuredMemoryUpdate,
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

export function submitApproval(
  client: ApiClient,
  decision: ApprovalDecision,
): Promise<ApprovalAck> {
  return request<ApprovalAck>(client, "POST", "/api/agent/approvals", { body: decision });
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

export function saveStructuredMemory(
  client: ApiClient,
  text: string,
): Promise<StructuredMemoryUpdate> {
  return request<StructuredMemoryUpdate>(client, "PUT", "/api/memories/structured", {
    body: { text },
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
