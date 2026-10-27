export type PetState = "idle" | "thinking" | "speaking" | "executing" | "sleeping";

export type MemoryCategory = "profile" | "preference" | "habit" | "event" | "interaction";

export interface MemorySourceRef {
  id: string;
  kind: string;
  category: string | null;
  date: string;
  text: string;
}

export type ServerEvent =
  | { type: "status"; state: PetState }
  | { type: "chat.chunk"; message_id: string; text: string }
  | { type: "chat.done"; message_id: string; text: string; sources: MemorySourceRef[] };

export interface ChatReplyResponse {
  message_id: string;
  text: string;
  recorded: boolean;
  sources: MemorySourceRef[];
}

export interface ChatHistoryItem {
  id: number;
  date: string;
  role: string;
  content: string;
  created_at: string;
}

export interface ChatHistoryResponse {
  items: ChatHistoryItem[];
  has_more: boolean;
}

export type ChatHistoryQuery = {
  date_from?: string;
  date_to?: string;
  keyword?: string;
  limit?: number;
  offset?: number;
};

export interface ActiveModelInfo {
  provider: string;
  model: string;
  source: "database" | "env";
}

export interface ActiveLlmResponse {
  chat: ActiveModelInfo | null;
  consolidate: ActiveModelInfo | null;
}

export interface ActiveSelection {
  provider: string;
  model: string;
}

export interface ActiveLlmRequest {
  chat?: ActiveSelection;
  consolidate?: ActiveSelection;
}

export interface ModelInfo {
  name: string;
  id: string;
  api: string;
  roles: string[];
}

export interface ProviderInfo {
  name: string;
  display_name: string;
  available: boolean;
  reason: string | null;
  models: ModelInfo[];
}

export interface DefaultInfo {
  provider: string;
  model: string;
}

export interface LlmProvidersResponse {
  version: string;
  providers: ProviderInfo[];
  defaults: Record<string, DefaultInfo>;
  warnings: string[];
  last_reload_errors: string[];
}

export interface ReloadResponse {
  ok: boolean;
  version: string;
  warnings: string[];
}

export interface MemorySource {
  type: string;
  date: string;
}

export interface MemoryItem {
  id: string;
  category: string;
  title: string;
  content: string;
  importance: number;
  source: MemorySource;
  created_at: string;
  updated_at: string;
}

export interface SkippedMemory {
  name: string;
  reason: string;
}

export interface DailyRecord {
  id: number;
  date: string;
  role: string;
  content: string;
  created_at: string;
}

export interface MemoriesResponse {
  temporary_mode: boolean;
  core_ids: string[];
  structured: MemoryItem[];
  skipped: SkippedMemory[];
  daily_memory: Record<string, string[]>;
  daily_records: DailyRecord[] | null;
}

export type MemoriesQuery = {
  category?: MemoryCategory;
  date?: string;
};

export interface MemoryPatch {
  title?: string;
  content?: string;
  category?: MemoryCategory;
  importance?: number;
}

export interface DeleteMemoryResponse {
  id: string;
  deleted: boolean;
}

export interface TemporaryModeResponse {
  temporary_mode: boolean;
}

export interface CoreMemoriesResponse {
  core_ids: string[];
}

export interface PersonaItem {
  id: string;
  name: string | null;
  available: boolean;
  reason: string | null;
  active: boolean;
}

export interface PersonasResponse {
  active: string;
  personas: PersonaItem[];
}
