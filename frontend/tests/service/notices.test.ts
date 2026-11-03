import { describe, expect, it } from "vitest";
import { noticeLabels } from "../../src/service/notices";

describe("notice labels", () => {
  it("maps memory_unloaded to user-facing text", () => {
    expect(noticeLabels(["memory_unloaded"])).toEqual(["本轮结构化记忆未载入"]);
  });

  it("ignores missing and unknown codes", () => {
    expect(noticeLabels(undefined)).toEqual([]);
    expect(noticeLabels([])).toEqual([]);
    expect(noticeLabels(["future_code", "memory_unloaded"])).toEqual(["本轮结构化记忆未载入"]);
  });
});
