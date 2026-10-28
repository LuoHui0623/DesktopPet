import type { MemorySourceRef } from "../../api/types";

export function sourceHeading(source: MemorySourceRef): string {
  return [source.kind, source.date].join(" · ");
}
