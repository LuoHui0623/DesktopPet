import type { MemoryCategory, MemoryItem } from "../../api/types";
import { categoryLabel } from "../memory-labels";

export { categoryLabel };

export const CATEGORY_OPTIONS: MemoryCategory[] = [
  "profile",
  "preference",
  "habit",
  "event",
  "interaction",
];

export function filterByCategory(
  items: MemoryItem[],
  category: MemoryCategory | null,
): MemoryItem[] {
  return category === null ? items : items.filter((item) => item.category === category);
}

export function moveId(ids: string[], id: string, delta: -1 | 1): string[] {
  const index = ids.indexOf(id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= ids.length) return ids;
  const next = [...ids];
  const moved = next[index] as string;
  next[index] = next[target] as string;
  next[target] = moved;
  return next;
}

export interface CoreEntry {
  id: string;
  memory: MemoryItem | null;
}

export function coreEntries(items: MemoryItem[], coreIds: string[]): CoreEntry[] {
  const byId = new Map(items.map((item) => [item.id, item]));
  return coreIds.map((id) => ({ id, memory: byId.get(id) ?? null }));
}

export function allDates(dailyMemory: Record<string, string[]>): string[] {
  return Object.values(dailyMemory).flat().sort().reverse();
}
