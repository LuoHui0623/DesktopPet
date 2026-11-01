import { request, type ApiClient } from "./client";
import type {
  ActiveLlmRequest,
  ActiveLlmResponse,
  ApprovalAck,
  ApprovalDecision,
  AvatarsResponse,
  ChatHistoryQuery,
  ChatHistoryResponse,
  ChatReplyResponse,
  InteractionConfig,
  InteractionKind,
  InteractionResponse,
  LlmProvidersResponse,
  MemoriesQuery,
  MemoriesResponse,
  MenuResponse,
  PerceptionConfig,
  PerceptionIngestResponse,
  PerceptionProcessDecision,
  PerceptionSample,
  PerceptionStateResponse,
  PersonasResponse,
  ReloadResponse,
  SchedulerConfig,
  SchedulerLedger,
  SettingsView,
  StructuredMemoryUpdate,
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

export function listAvatars(client: ApiClient): Promise<AvatarsResponse> {
  return request<AvatarsResponse>(client, "GET", "/api/avatars");
}

export function activateAvatar(client: ApiClient, id: string): Promise<AvatarsResponse> {
  return request<AvatarsResponse>(client, "PUT", "/api/avatars/active", { body: { id } });
}

export function getMenu(client: ApiClient): Promise<MenuResponse> {
  return request<MenuResponse>(client, "GET", "/api/menu");
}

export function getSchedulerConfig(client: ApiClient): Promise<SchedulerConfig> {
  return request<SchedulerConfig>(client, "GET", "/api/scheduler/config");
}

export function updateSchedulerConfig(
  client: ApiClient,
  config: SchedulerConfig,
): Promise<SchedulerConfig> {
  return request<SchedulerConfig>(client, "PUT", "/api/scheduler/config", { body: config });
}

export function ingestPerception(
  client: ApiClient,
  sample: PerceptionSample,
): Promise<PerceptionIngestResponse> {
  return request<PerceptionIngestResponse>(client, "POST", "/api/perception", { body: sample });
}

export function getPerceptionState(client: ApiClient): Promise<PerceptionStateResponse> {
  return request<PerceptionStateResponse>(client, "GET", "/api/perception/state");
}

export function decidePerceptionProcess(
  client: ApiClient,
  decision: PerceptionProcessDecision,
): Promise<PerceptionStateResponse> {
  return request<PerceptionStateResponse>(client, "POST", "/api/perception/processes/decision", {
    body: decision,
  });
}

export function reportInteraction(
  client: ApiClient,
  kind: InteractionKind,
): Promise<InteractionResponse> {
  return request<InteractionResponse>(client, "POST", "/api/interaction", { body: { kind } });
}

export function getInteractionConfig(client: ApiClient): Promise<InteractionConfig> {
  return request<InteractionConfig>(client, "GET", "/api/interaction/config");
}

export function getSettings(client: ApiClient): Promise<SettingsView> {
  return request<SettingsView>(client, "GET", "/api/settings");
}

export function updateSettingsPerception(
  client: ApiClient,
  config: PerceptionConfig,
): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/perception", { body: config });
}

export function updateSettingsProactive(
  client: ApiClient,
  config: SchedulerConfig,
): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/proactive", { body: config });
}

export function updateSettingsAvatar(client: ApiClient, id: string): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/avatar", { body: { id } });
}

export function updateSettingsInteraction(
  client: ApiClient,
  config: InteractionConfig,
): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/interaction", { body: config });
}

export function getSchedulerLedger(client: ApiClient): Promise<SchedulerLedger> {
  return request<SchedulerLedger>(client, "GET", "/api/scheduler/ledger");
}

export function clearPerceptionRecords(client: ApiClient): Promise<{ cleared: boolean }> {
  return request<{ cleared: boolean }>(client, "POST", "/api/perception/clear");
}
