const NOTICE_LABELS: Record<string, string> = {
  memory_unloaded: "本轮结构化记忆未载入",
};

/** 后端 notices 是稳定 code，展示文案由前端映射；未知 code 不打扰用户（D-30）。 */
export function noticeLabels(notices: readonly string[] | undefined): string[] {
  if (!notices) return [];
  return notices.flatMap((code) => {
    const label = NOTICE_LABELS[code];
    return label ? [label] : [];
  });
}
