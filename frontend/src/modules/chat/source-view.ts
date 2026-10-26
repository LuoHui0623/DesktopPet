import type { MemorySourceRef } from "../../api/types";
import { categoryLabel } from "../memory-labels";

export function sourceHeading(source: MemorySourceRef): string {
  const parts = [source.kind];
  if (source.category) parts.push(categoryLabel(source.category));
  parts.push(source.date);
  return parts.join(" · ");
}
