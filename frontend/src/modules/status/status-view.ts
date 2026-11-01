const PURPOSE_LABELS: Record<string, string> = {
  chat: "对话",
  consolidate: "记忆整理",
};

export function formatTokens(value: number | null): string {
  return value === null ? "未知" : String(value);
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return "未知";
  if (ms < 1000) return `${ms} 毫秒`;
  return `${(ms / 1000).toFixed(1)} 秒`;
}

export function callLabel(purpose: string | null): string {
  if (purpose === null) return "未知调用";
  return PURPOSE_LABELS[purpose] ?? purpose;
}

export function turnStateLabel(finished: boolean): string {
  return finished ? "已完成" : "进行中";
}
