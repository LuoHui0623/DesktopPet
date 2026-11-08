export type PetState = "idle" | "thinking" | "speaking" | "executing" | "sleeping";

export interface MemorySourceRef {
  id: string;
  kind: string;
  date: string;
  text: string;
}

/** 一轮回复中的单条发言；`at` 为带时区的生成时间，由服务端给出。 */
export interface ReplyMessage {
  message_id: string;
  text: string;
  at: string;
  sources: MemorySourceRef[];
}

export interface PreviewHunk {
  header: string;
  removed: string[];
  added: string[];
}

/** 一个文件的待审批净差异；文件过大时折叠，只保留改动数量。 */
export interface FilePreview {
  resource: string;
  path: string;
  op: string;
  created: boolean;
  deleted: boolean;
  authors: string[];
  hunk_count: number;
  folded: boolean;
  hunks: PreviewHunk[];
}

export interface ApprovalItem {
  approval_id: string;
  run_id: string;
  topic: string;
  task: string;
  content: string;
  tools: string[];
  persona_id: string | null;
  persona_version: number | null;
  main_task_id: string | null;
  subtask_id: string | null;
  source: string | null;
  previews: FilePreview[];
  model: string | null;
  /** 读取票据：动作、目标与参数摘要；结算票据为空。 */
  action?: string | null;
  resource?: string | null;
  path?: string | null;
  parameters?: string | null;
  plan_id?: string | null;
}

export interface ApprovalDecision {
  approval_id: string;
  approved: boolean;
}

export interface ApprovalAck {
  approval_id: string;
  approved: boolean;
}

export interface UsageCall {
  provider: string | null;
  model: string | null;
  purpose: string | null;
  turn_id: string | null;
  run_id: string | null;
  prompt_hash: string | null;
  started_at: string;
  finished_at: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  usage_raw: string | null;
  latency_ms: number | null;
  success: boolean | null;
  prompt_text: string | null;
  reply_text: string | null;
  reply_raw: string | null;
}

export type UsagePanelMode = "dev" | "prod";

/** 状态窗口显示的当前轮；正式模式下正文与调用方信息已被后端清空。 */
export interface UsagePanelTurn {
  turn_id: string;
  started_at: string;
  finished_at: string | null;
  finished: boolean;
  tokens_in: number | null;
  tokens_out: number | null;
  tokens_complete: boolean;
  model_time_ms: number;
  calls: UsageCall[];
}

export type ServerEvent =
  | { type: "status"; state: PetState }
  | { type: "chat.chunk"; message_id: string; text: string }
  | {
      type: "chat.done";
      message_id: string;
      text: string;
      sources: MemorySourceRef[];
      messages?: ReplyMessage[];
      notices?: string[];
    }
  | { type: "approval.request"; plan_id: string; items: ApprovalItem[] }
  | {
      type: "proactive.message";
      id: string;
      text: string;
      topic: string;
      at: string;
      messages?: ReplyMessage[];
      notices?: string[];
    }
  | { type: "usage.call.started"; call: UsageCall }
  | { type: "usage.call.finished"; call: UsageCall }
  | { type: "usage.turn.started"; turn_id: string; at: string }
  | {
      type: "usage.turn.finished";
      turn_id: string;
      tokens_in: number | null;
      tokens_out: number | null;
      tokens_complete: boolean;
      model_time_ms: number;
      started_at: string | null;
      finished_at: string | null;
      calls: UsageCall[];
    }
  | {
      type: "usage.panel.snapshot";
      mode: UsagePanelMode;
      turn: UsagePanelTurn | null;
    };

export interface ChatReplyResponse {
  message_id: string;
  text: string;
  recorded: boolean;
  sources: MemorySourceRef[];
  messages?: ReplyMessage[];
  /** 用户可见的系统提示 code，如 memory_unloaded；展示文案由前端映射（D-30）。 */
  notices?: string[];
}

export interface ChatHistoryItem {
  id: number;
  event_time: string;
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
  event_time: string;
  role: string;
  content: string;
  created_at: string;
}

export interface MemoriesResponse {
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
  static: string;
  states: Record<string, AvatarState>;
}

export interface AvatarsResponse {
  persona: string;
  active: string | null;
  preset_states: string[];
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
