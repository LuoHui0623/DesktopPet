import type { MemoryCategory } from "../api/types";

export const CATEGORY_LABELS: Record<MemoryCategory, string> = {
  profile: "个人资料",
  preference: "偏好",
  habit: "习惯",
  event: "事件",
  interaction: "互动",
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category as MemoryCategory] ?? category;
}
