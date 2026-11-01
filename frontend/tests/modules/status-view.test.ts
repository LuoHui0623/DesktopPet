import { describe, expect, it } from "vitest";
import {
  callLabel,
  formatDuration,
  formatTokens,
  turnStateLabel,
} from "../../src/modules/status/status-view";

describe("status view formatting", () => {
  it("shows unknown tokens as an explicit unknown value", () => {
    expect(formatTokens(null)).toBe("未知");
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(12)).toBe("12");
  });

  it("formats durations in milliseconds and seconds", () => {
    expect(formatDuration(null)).toBe("未知");
    expect(formatDuration(0)).toBe("0 毫秒");
    expect(formatDuration(860)).toBe("860 毫秒");
    expect(formatDuration(1200)).toBe("1.2 秒");
  });

  it("labels known purposes and keeps unknown ones visible", () => {
    expect(callLabel("chat")).toBe("对话");
    expect(callLabel("consolidate")).toBe("记忆整理");
    expect(callLabel("dispatch")).toBe("dispatch");
    expect(callLabel(null)).toBe("未知调用");
  });

  it("labels the round state", () => {
    expect(turnStateLabel(false)).toBe("进行中");
    expect(turnStateLabel(true)).toBe("已完成");
  });
});
