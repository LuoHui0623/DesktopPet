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
  EntryAddResult,
  EntryListResponse,
  EntryUpdateRequest,
  InteractionConfig,
  InteractionKind,
  InteractionResponse,
  LlmProvidersResponse,
  MemoriesQuery,
  MemoriesResponse,
  MemoryEntriesQuery,
  MemoryEntryCreate,
  MemoryEntryView,
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
  TagListResponse,
  TagMergePreview,
  TagMergeRequest,
  TagMergeResult,
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

export function listMemoryEntries(
  client: ApiClient,
  query: MemoryEntriesQuery = {},
): Promise<EntryListResponse> {
  return request<EntryListResponse>(client, "GET", "/api/memories/entries", { query });
}

export function createMemoryEntry(
  client: ApiClient,
  payload: MemoryEntryCreate,
): Promise<EntryAddResult> {
  return request<EntryAddResult>(client, "POST", "/api/memories/entries", { body: payload });
}

export function updateMemoryEntry(
  client: ApiClient,
  entryId: number,
  payload: EntryUpdateRequest,
): Promise<MemoryEntryView> {
  return request<MemoryEntryView>(client, "PUT", `/api/memories/entries/${entryId}`, {
    body: payload,
  });
}

export function enableMemoryEntry(client: ApiClient, entryId: number): Promise<MemoryEntryView> {
  return request<MemoryEntryView>(client, "POST", `/api/memories/entries/${entryId}/enable`);
}

export function disableMemoryEntry(
  client: ApiClient,
  entryId: number,
  reason: string,
): Promise<MemoryEntryView> {
  return request<MemoryEntryView>(client, "POST", `/api/memories/entries/${entryId}/disable`, {
    body: { reason },
  });
}

export function listMemoryTags(client: ApiClient): Promise<TagListResponse> {
  return request<TagListResponse>(client, "GET", "/api/memories/tags");
}

export function previewTagMerge(
  client: ApiClient,
  payload: TagMergeRequest,
): Promise<TagMergePreview> {
  return request<TagMergePreview>(client, "POST", "/api/memories/tags/merge/preview", {
    body: payload,
  });
}

export function mergeMemoryTags(
  client: ApiClient,
  payload: TagMergeRequest,
): Promise<TagMergeResult> {
  return request<TagMergeResult>(client, "POST", "/api/memories/tags/merge", { body: payload });
}

export function listPersonas(client: ApiClient): Promise<PersonasResponse> {
  return request<PersonasResponse>(client, "GET", "/api/personas");
}

export function listAvatars(client: ApiClient): Promise<AvatarsResponse> {
  return request<AvatarsResponse>(client, "GET", "/api/avatars");
}

export function getMenu(client: ApiClient): Promise<MenuResponse> {
  return request<MenuResponse>(client, "GET", "/api/menu");
}

export function getSchedulerConfig(client: ApiClient): Promise<SchedulerConfig> {
  return request<SchedulerConfig>(client, "GET", "/api/scheduler/config");
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

function settingsHeaders(version: number): Record<string, string> {
  return { "If-Match": String(version) };
}

export function updateSettingsPerception(
  client: ApiClient,
  config: PerceptionConfig,
  version: number,
): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/perception", {
    body: config,
    headers: settingsHeaders(version),
  });
}

export function updateSettingsProactive(
  client: ApiClient,
  config: SchedulerConfig,
  version: number,
): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/proactive", {
    body: config,
    headers: settingsHeaders(version),
  });
}

export function updateSettingsAvatar(
  client: ApiClient,
  id: string,
  version: number,
): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/avatar", {
    body: { id },
    headers: settingsHeaders(version),
  });
}

export function updateSettingsPersona(
  client: ApiClient,
  id: string,
  version: number,
): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/persona", {
    body: { id },
    headers: settingsHeaders(version),
  });
}

export function updateSettingsModel(
  client: ApiClient,
  selection: ActiveLlmRequest,
  version: number,
): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/model", {
    body: selection,
    headers: settingsHeaders(version),
  });
}

export function updateSettingsInteraction(
  client: ApiClient,
  config: InteractionConfig,
  version: number,
): Promise<SettingsView> {
  return request<SettingsView>(client, "PUT", "/api/settings/interaction", {
    body: config,
    headers: settingsHeaders(version),
  });
}

export function getSchedulerLedger(client: ApiClient): Promise<SchedulerLedger> {
  return request<SchedulerLedger>(client, "GET", "/api/scheduler/ledger");
}

export function clearPerceptionRecords(client: ApiClient): Promise<{ cleared: boolean }> {
  return request<{ cleared: boolean }>(client, "POST", "/api/perception/clear");
}
