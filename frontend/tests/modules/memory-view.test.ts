import { describe, expect, it } from "vitest";
import { allDates, numberedLines } from "../../src/modules/settings/memory-view";

describe("numberedLines", () => {
  it("numbers every line starting at 1", () => {
    expect(numberedLines("## 主人信息\n- 喜欢拿铁")).toEqual([
      { line: 1, text: "## 主人信息" },
      { line: 2, text: "- 喜欢拿铁" },
    ]);
  });

  it("keeps blank lines so numbers match the file", () => {
    expect(numberedLines("a\n\nb")).toEqual([
      { line: 1, text: "a" },
      { line: 2, text: "" },
      { line: 3, text: "b" },
    ]);
  });

  it("ignores a single trailing newline", () => {
    expect(numberedLines("a\nb\n")).toHaveLength(2);
  });

  it("returns nothing for empty text", () => {
    expect(numberedLines("")).toEqual([]);
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

  it("returns nothing when there is no daily memory", () => {
    expect(allDates({})).toEqual([]);
  });
});
