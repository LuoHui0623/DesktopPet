import type { PersonaItem } from "../../api/types";

export function personaStatusText(item: PersonaItem): string {
  if (item.active) return "使用中";
  return item.available ? "可用" : "不可用";
}

export function canActivate(item: PersonaItem): boolean {
  return item.available && !item.active;
}
