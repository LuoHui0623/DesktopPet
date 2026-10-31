/** 时间戳里的时钟部分：日期 + 时:分(:秒)(小数秒)(时区)。 */
const CLOCK_PATTERN =
  /^\d{4}-\d{2}-\d{2}[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$/;

/**
 * 取出时间戳里的 `HH:MM:SS`，供气泡与历史面板复用。
 *
 * 直接按字面量截取而不是转成本地时间，这样带时区的 `at` 与不带时区的
 * `created_at` 都显示成后端记录的钟点，避免两侧出现时差。
 */
export function formatClock(value: string | null | undefined): string {
  if (!value) return "";
  const matched = CLOCK_PATTERN.exec(value.trim());
  if (!matched) return "";
  const [, hour, minute, second = "00"] = matched;
  return `${hour}:${minute}:${second}`;
}

/** 消息行首的 `[角色][HH:MM:SS]` 标记；没有可用时间时只给角色。 */
export function roleStamp(role: string, value: string | null | undefined): string {
  const clock = formatClock(value);
  return clock ? `[${role}][${clock}]` : `[${role}]`;
}
