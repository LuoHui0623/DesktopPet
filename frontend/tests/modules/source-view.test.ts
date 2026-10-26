import { describe, expect, it } from "vitest";
import { sourceHeading } from "../../src/modules/chat/source-view";

describe("sourceHeading", () => {
  it("shows the kind, category label and date of a structured memory", () => {
    expect(
      sourceHeading({
        id: "m_20261018_001",
        kind: "结构化记忆",
        category: "preference",
        date: "2026-10-18",
        text: "喜欢美式咖啡",
      }),
    ).toBe("结构化记忆 · 偏好 · 2026-10-18");
  });

  it("omits the category for daily conversation sources", () => {
    expect(
      sourceHeading({
        id: "2026-10-17",
        kind: "每日对话",
        category: null,
        date: "2026-10-17",
        text: "昨天聊了茶",
      }),
    ).toBe("每日对话 · 2026-10-17");
  });
});
