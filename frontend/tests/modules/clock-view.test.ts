import { describe, expect, it } from "vitest";
import { formatClock, formatDay } from "../../src/modules/chat/clock-view";

describe("formatClock", () => {
  it("renders hours minutes and seconds of a timestamp with an offset", () => {
    expect(formatClock("2026-10-29T10:00:07+08:00")).toBe("10:00:07");
  });

  it("renders the seconds of a history record written without an offset", () => {
    expect(formatClock("2026-10-29 09:05:03")).toBe("09:05:03");
  });

  it("falls back to zero seconds when the timestamp stops at the minute", () => {
    expect(formatClock("2026-10-29T10:00+08:00")).toBe("10:00:00");
  });

  it("returns an empty string when there is no usable time", () => {
    expect(formatClock("")).toBe("");
    expect(formatClock("不是时间")).toBe("");
    expect(formatClock(null)).toBe("");
  });
});

describe("formatDay", () => {
  it("takes the calendar day from an event timestamp", () => {
    expect(formatDay("2026-10-29T10:00:07+08:00")).toBe("2026-10-29");
  });

  it("returns an empty string when there is no usable date", () => {
    expect(formatDay("")).toBe("");
    expect(formatDay("不是时间")).toBe("");
    expect(formatDay(undefined)).toBe("");
  });
});
