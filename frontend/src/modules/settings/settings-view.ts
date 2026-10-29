import type { AvatarPack, InteractionKind, SchedulerLedger, SettingsView } from "../../api/types";

export const INTERACTION_LABELS: Record<InteractionKind, string> = {
  click: "单击上报",
  hover: "悬停上报",
  drag: "拖动上报",
  menu: "右键菜单上报",
};

export const INTERACTION_KINDS: InteractionKind[] = ["click", "hover", "drag", "menu"];

export const TOPIC_LABELS: Record<string, string> = {
  greeting_return: "欢迎回来",
  sit_long: "久坐提醒",
  late_night: "深夜提醒",
  app_launch: "应用启动",
  app_quit: "应用退出",
  app_in_use: "正在使用",
  busy_released: "忙完回来",
  idle_enter: "离开告别",
  idle_return: "回归问候",
};

export interface FieldResult {
  value: number;
  message: string | null;
}

/** 输入校验区间，与后端 api/schemas 的 ge/le 保持一致；真源仍是后端。 */
export const LIMITS = {
  idleAwayMinutes: { min: 1, max: 720 },
  sitMinutes: { min: 15, max: 720 },
  minIntervalMinutes: { min: 0, max: 1440 },
  maxPerDay: { min: 0, max: 100 },
  hoverDwellMs: { min: 100, max: 3000 },
  reportIntervalMs: { min: 0, max: 10_000 },
  reactionMs: { min: 0, max: 3000 },
} as const;

// 数字输入解析：留空或非法时回退到后端下发的当前值，不引入前端默认值。
export function parseNumberField(
  raw: string,
  fallback: number,
  limits: { min: number; max: number },
): FieldResult {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: fallback, message: "不能为空" };
  const parsed = Number(trimmed);
  if (!Number.isInteger(parsed)) return { value: fallback, message: "必须是整数" };
  if (parsed < limits.min || parsed > limits.max) {
    return { value: fallback, message: `必须在 ${limits.min} 到 ${limits.max} 之间` };
  }
  return { value: parsed, message: null };
}

export function dndSummary(settings: SettingsView): string {
  const { dnd_start: start, dnd_end: end } = settings.proactive;
  if (start === end) return "未设置勿扰时段";
  return start < end ? `勿扰 ${start}–${end}` : `勿扰 ${start}–次日 ${end}`;
}

export function ledgerSummary(ledger: SchedulerLedger | null): string {
  if (ledger === null) return "加载中…";
  const topics = Object.entries(ledger.topics)
    .map(([key, count]) => `${TOPIC_LABELS[key] ?? key} ${count} 次`)
    .join("、");
  const used = `今日已主动 ${ledger.total} 次`;
  return topics === "" ? used : `${used}（${topics}）`;
}

export function previewFrame(pack: AvatarPack): string | null {
  const order = ["idle", pack.fallback, ...Object.keys(pack.states)];
  for (const key of order) {
    const state = pack.states[key];
    if (state !== undefined && state.frames.length > 0) return state.frames[0];
  }
  return null;
}

export function packStatesText(pack: AvatarPack): string {
  const groups = Object.values(pack.states).reduce((sum, state) => sum + state.frames.length, 0);
  return `${Object.keys(pack.states).length} 组状态 / ${groups} 帧`;
}
