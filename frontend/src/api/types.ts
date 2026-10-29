export type PetState = "idle" | "thinking" | "speaking" | "executing" | "sleeping";

export interface MemorySourceRef {
  id: string;
  kind: string;
  date: string;
  text: string;
}

export interface ApprovalItem {
  approval_id: string;
  run_id: string;
  capability: string;
  task: string;
  content: string;
  tools: string[];
}

export interface ApprovalDecision {
  approval_id: string;
  approved: boolean;
}

export interface ApprovalAck {
  approval_id: string;
  approved: boolean;
}

export type ServerEvent =
  | { type: "status"; state: PetState }
  | { type: "chat.chunk"; message_id: string; text: string }
  | { type: "chat.done"; message_id: string; text: string; sources: MemorySourceRef[] }
  | { type: "approval.request"; plan_id: string; items: ApprovalItem[] }
  | { type: "proactive.message"; id: string; text: string; topic: string; at: string };

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

export interface DailyRecord {
  id: number;
  date: string;
  role: string;
  content: string;
  created_at: string;
}

export interface MemoriesResponse {
  temporary_mode: boolean;
  structured: string;
  daily_memory: Record<string, string[]>;
  daily_records: DailyRecord[] | null;
}

export type MemoriesQuery = {
  date?: string;
};

export interface StructuredMemoryUpdate {
  text: string;
}

export interface TemporaryModeResponse {
  temporary_mode: boolean;
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

export interface AvatarFrameSize {
  width: number;
  height: number;
}

export interface AvatarState {
  frames: string[];
  frame_ms: number;
}

export interface AvatarPack {
  id: string;
  name: string;
  active: boolean;
  frame: AvatarFrameSize;
  fallback: string;
  states: Record<string, AvatarState>;
}

export interface AvatarsResponse {
  active: string;
  packs: AvatarPack[];
}

export type PerceptionKind =
  "idle" | "foreground" | "fullscreen" | "locked" | "processes" | "process_candidate";

export type PerceptionValue = number | boolean | string | string[] | null;

export interface PerceptionSample {
  kind: PerceptionKind;
  value: PerceptionValue;
  at: string;
}

export interface PerceptionIngestResponse {
  accepted: boolean;
  reason: string | null;
  candidates: string[];
}

export interface PerceptionStateResponse {
  enabled: boolean;
  foreground_enabled: boolean;
  presence: "active" | "away" | "unknown";
  busy: boolean;
  idle_seconds: number | null;
  foreground: string | null;
  whitelist: string[];
  muted: string[];
  candidates: string[];
}

export interface PerceptionProcessDecision {
  exe: string;
  decision: "allow" | "ignore";
}

export interface PerceptionConfig {
  enabled: boolean;
  foreground_enabled: boolean;
  idle_away_minutes: number;
  sit_minutes: number;
}

export type InteractionKind = "click" | "hover" | "drag" | "menu";

export interface InteractionResponse {
  accepted: boolean;
  reason: string | null;
}

export interface InteractionConfig {
  report_enabled_kinds: InteractionKind[];
  hover_dwell_ms: number;
  report_interval_ms: number;
  reaction_ms: number;
}

export interface MenuItem {
  id: string;
  label: string;
  enabled: boolean;
  checked: boolean | null;
  children: MenuItem[];
}

export interface MenuResponse {
  items: MenuItem[];
}

export interface SchedulerConfig {
  enabled: boolean;
  min_interval_minutes: number;
  max_per_day: number;
  dnd_start: string;
  dnd_end: string;
}

export interface SchedulerLedger {
  date: string;
  topics: Record<string, number>;
  total: number;
}

export interface SettingsView {
  perception: PerceptionConfig;
  proactive: SchedulerConfig;
  avatar: AvatarsResponse;
  interaction: InteractionConfig;
}
