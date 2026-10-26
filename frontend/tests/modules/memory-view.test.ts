import { describe, expect, it } from "vitest";
import type { MemoryItem } from "../../src/api/types";
import {
  allDates,
  categoryLabel,
  coreEntries,
  filterByCategory,
  moveId,
} from "../../src/modules/settings/memory-view";

function item(id: string, category: string, title = id): MemoryItem {
  return {
    id,
    category,
    title,
    content: `${title} 的内容`,
    importance: 3,
    source: { type: "keyword", date: "2026-10-18" },
    created_at: "2026-10-18T09:00:00+08:00",
    updated_at: "2026-10-18T09:00:00+08:00",
  };
}

describe("moveId", () => {
  it("swaps an id with its neighbour", () => {
    expect(moveId(["a", "b", "c"], "b", -1)).toEqual(["b", "a", "c"]);
    expect(moveId(["a", "b", "c"], "b", 1)).toEqual(["a", "c", "b"]);
  });

  it("does not move past either end, and leaves the input untouched", () => {
    const ids = ["a", "b"];
    expect(moveId(ids, "a", -1)).toBe(ids);
    expect(moveId(ids, "b", 1)).toBe(ids);
    expect(ids).toEqual(["a", "b"]);
  });

  it("ignores ids that are not in the list", () => {
    const ids = ["a", "b"];
    expect(moveId(ids, "z", 1)).toBe(ids);
  });
});

describe("filterByCategory", () => {
  const items = [item("m1", "preference"), item("m2", "habit")];

  it("returns everything when no category is chosen", () => {
    expect(filterByCategory(items, null)).toHaveLength(2);
  });

  it("keeps only the chosen category", () => {
    expect(filterByCategory(items, "habit").map((i) => i.id)).toEqual(["m2"]);
  });
});

describe("coreEntries", () => {
  it("keeps the core order and marks ids whose memory is gone", () => {
    const entries = coreEntries([item("m1", "profile"), item("m2", "event")], ["m2", "m9", "m1"]);
    expect(entries.map((entry) => [entry.id, entry.memory?.id ?? null])).toEqual([
      ["m2", "m2"],
      ["m9", null],
      ["m1", "m1"],
    ]);
  });
});

describe("allDates", () => {
  it("flattens month buckets and sorts newest first", () => {
    expect(
      allDates({
        "2026-09": ["2026-09-30"],
        "2026-10": ["2026-10-02", "2026-10-18"],
      }),
    ).toEqual(["2026-10-18", "2026-10-02", "2026-09-30"]);
  });
});

describe("categoryLabel", () => {
  it("shows Chinese labels and falls back to the raw value", () => {
    expect(categoryLabel("preference")).toBe("偏好");
    expect(categoryLabel("unknown")).toBe("unknown");
  });
});
