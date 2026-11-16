import { describe, expect, it } from "vitest";
import type { MemoryEntryView, TagPathChange } from "../../src/api/types";
import {
  allDates,
  annotatePathChange,
  groupEntriesByPath,
  parseTagsInput,
  pathLabel,
} from "../../src/modules/settings/memory-view";

function entry(
  id: number,
  tags: string[],
  overrides: Partial<MemoryEntryView> = {},
): MemoryEntryView {
  return {
    id,
    content: `内容${id}`,
    tags,
    status_state: "enabled",
    status_reason: null,
    revision: 1,
    created_at: "2026-11-13T00:00:00Z",
    updated_at: "2026-11-13T00:00:00Z",
    ...overrides,
  };
}

describe("allDates", () => {
  it("flattens month buckets and sorts newest first", () => {
    expect(
      allDates({
        "2026-09": ["2026-09-30"],
        "2026-10": ["2026-10-02", "2026-10-18"],
      }),
    ).toEqual(["2026-10-18", "2026-10-02", "2026-09-30"]);
  });

  it("returns nothing when there is no daily memory", () => {
    expect(allDates({})).toEqual([]);
  });
});

describe("pathLabel", () => {
  it("joins the ordered path with a slash separator", () => {
    expect(pathLabel(["食物", "水果"])).toBe("食物 / 水果");
  });

  it("returns an empty string for an empty path", () => {
    expect(pathLabel([])).toBe("");
  });
});

describe("groupEntriesByPath", () => {
  it("groups entries that share the same ordered path", () => {
    const groups = groupEntriesByPath([
      entry(1, ["食物", "水果"]),
      entry(2, ["食物", "水果"]),
      entry(3, ["饮食"]),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0]?.path).toEqual(["食物", "水果"]);
    expect(groups[0]?.entries.map((item) => item.id)).toEqual([1, 2]);
    expect(groups[1]?.path).toEqual(["饮食"]);
    expect(groups[1]?.entries.map((item) => item.id)).toEqual([3]);
  });

  it("keeps first-seen group order", () => {
    const groups = groupEntriesByPath([entry(1, ["乙"]), entry(2, ["甲"]), entry(3, ["乙"])]);
    expect(groups.map((group) => group.path)).toEqual([["乙"], ["甲"]]);
  });

  it("treats a different tag order as a different group", () => {
    const groups = groupEntriesByPath([entry(1, ["食物", "水果"]), entry(2, ["水果", "食物"])]);
    expect(groups).toHaveLength(2);
  });

  it("includes disabled entries with their reason", () => {
    const groups = groupEntriesByPath([
      entry(1, ["饮食"], { status_state: "disabled", status_reason: "已迁入人设" }),
    ]);
    expect(groups[0]?.entries[0]?.status_state).toBe("disabled");
    expect(groups[0]?.entries[0]?.status_reason).toBe("已迁入人设");
  });

  it("returns nothing for an empty list", () => {
    expect(groupEntriesByPath([])).toEqual([]);
  });
});

describe("annotatePathChange", () => {
  const change = (before: string[], after: string[]): TagPathChange => ({
    entry_id: 1,
    content: "喜欢苹果",
    before,
    after,
  });

  it("flags a title change when the first tag differs", () => {
    const annotated = annotatePathChange(change(["水果", "苹果"], ["食物", "苹果"]));
    expect(annotated.titleChanged).toBe(true);
    expect(annotated.folded).toBe(false);
  });

  it("flags folding when the after path is shorter", () => {
    const annotated = annotatePathChange(change(["食物", "水果"], ["食物"]));
    expect(annotated.titleChanged).toBe(false);
    expect(annotated.folded).toBe(true);
  });

  it("flags both when the title changes and the path folds", () => {
    const annotated = annotatePathChange(change(["水果", "食物"], ["食物"]));
    expect(annotated.titleChanged).toBe(true);
    expect(annotated.folded).toBe(true);
  });

  it("flags neither when the path is unchanged", () => {
    const annotated = annotatePathChange(change(["食物"], ["食物"]));
    expect(annotated.titleChanged).toBe(false);
    expect(annotated.folded).toBe(false);
  });

  it("preserves the original change fields", () => {
    const annotated = annotatePathChange(change(["水果"], ["食物"]));
    expect(annotated.entry_id).toBe(1);
    expect(annotated.content).toBe("喜欢苹果");
    expect(annotated.before).toEqual(["水果"]);
    expect(annotated.after).toEqual(["食物"]);
  });
});

describe("parseTagsInput", () => {
  it("splits an ordered path on slashes and trims each tag", () => {
    expect(parseTagsInput("食物 / 水果")).toEqual(["食物", "水果"]);
    expect(parseTagsInput("食物/水果")).toEqual(["食物", "水果"]);
  });

  it("drops empty segments and surrounding whitespace", () => {
    expect(parseTagsInput("  食物  ")).toEqual(["食物"]);
    expect(parseTagsInput("食物//水果")).toEqual(["食物", "水果"]);
    expect(parseTagsInput("食物 / / 水果")).toEqual(["食物", "水果"]);
  });

  it("returns nothing for blank input", () => {
    expect(parseTagsInput("")).toEqual([]);
    expect(parseTagsInput("   ")).toEqual([]);
    expect(parseTagsInput("/")).toEqual([]);
  });

  it("keeps a single tag without a separator", () => {
    expect(parseTagsInput("饮食")).toEqual(["饮食"]);
  });
});
