import { describe, expect, it } from "vitest";
import { sourceHeading } from "../../src/modules/chat/source-view";

describe("sourceHeading", () => {
  it("shows the kind and date of a structured memory", () => {
    expect(
      sourceHeading({
        id: "structured",
        kind: "结构化记忆",
        date: "2026-10-18",
        text: "喜欢美式咖啡",
      }),
    ).toBe("结构化记忆 · 2026-10-18");
  });

  it("shows the kind and date of a daily conversation source", () => {
    expect(
      sourceHeading({
        id: "2026-10-17",
        kind: "每日对话",
        date: "2026-10-17",
        text: "昨天聊了茶",
      }),
    ).toBe("每日对话 · 2026-10-17");
  });
});
