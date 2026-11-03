import { describe, expect, it } from "vitest";
import type { AvatarPack, SettingsView } from "../../src/api/types";
import {
  INTERACTION_KINDS,
  INTERACTION_LABELS,
  LIMITS,
  dndSummary,
  ledgerSummary,
  packStatesText,
  parseNumberField,
  previewFrame,
} from "../../src/modules/settings/settings-view";

function makeView(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    perception: {
      enabled: true,
      foreground_enabled: true,
      idle_away_minutes: 10,
      sit_minutes: 90,
    },
    proactive: {
      enabled: true,
      min_interval_minutes: 60,
      max_per_day: 20,
      dnd_start: "22:30",
      dnd_end: "08:00",
    },
    avatar: { persona: "芙芙", active: "default", preset_states: [], packs: [] },
    interaction: {
      report_enabled_kinds: ["click"],
      hover_dwell_ms: 300,
      report_interval_ms: 1000,
      reaction_ms: 220,
    },
    ...overrides,
  };
}

function makePack(id: string, states: Record<string, string[]>): AvatarPack {
  return {
    id,
    name: `包 ${id}`,
    active: id === "default",
    frame: { width: 240, height: 240 },
    fallback: "thinking",
    static: "static.png",
    states: Object.fromEntries(
      Object.entries(states).map(([key, frames]) => [key, { frames, frame_ms: 160 }]),
    ),
  };
}

describe("parseNumberField", () => {
  it("accepts an in-range integer", () => {
    expect(parseNumberField("20", 10, LIMITS.idleAwayMinutes)).toEqual({
      value: 20,
      message: null,
    });
    expect(parseNumberField(" 45 ", 10, LIMITS.sitMinutes)).toEqual({ value: 45, message: null });
    expect(parseNumberField("0", 5, LIMITS.maxPerDay)).toEqual({ value: 0, message: null });
  });

  it("keeps the backend value when the input is unusable", () => {
    for (const raw of ["", "  ", "abc", "1.5", "-1", "721"]) {
      const result = parseNumberField(raw, 33, LIMITS.idleAwayMinutes);
      expect(result.value).toBe(33);
      expect(result.message).not.toBeNull();
    }
  });

  it("quotes the backend range in the message", () => {
    expect(parseNumberField("900", 90, LIMITS.sitMinutes).message).toBe("必须在 15 到 720 之间");
  });
});

describe("dndSummary", () => {
  it("marks the crossing of midnight", () => {
    expect(dndSummary(makeView())).toBe("勿扰 22:30–次日 08:00");
  });

  it("reads a same-day window without the next-day prefix", () => {
    const view = makeView();
    view.proactive = { ...view.proactive, dnd_start: "12:00", dnd_end: "13:00" };
    expect(dndSummary(view)).toBe("勿扰 12:00–13:00");
  });

  it("treats identical bounds as no quiet hours", () => {
    const view = makeView();
    view.proactive = { ...view.proactive, dnd_start: "08:00", dnd_end: "08:00" };
    expect(dndSummary(view)).toBe("未设置勿扰时段");
  });
});

describe("ledgerSummary", () => {
  it("waits for the backend ledger", () => {
    expect(ledgerSummary(null)).toBe("加载中…");
  });

  it("counts topics with their labels and keeps unknown keys", () => {
    expect(
      ledgerSummary({
        date: "2026-10-29",
        topics: { sit_long: 2, greeting_return: 1, mystery_topic: 3 },
        total: 6,
      }),
    ).toBe("今日已主动 6 次（久坐提醒 2 次、欢迎回来 1 次、mystery_topic 3 次）");
  });

  it("has nothing to list on a quiet day", () => {
    expect(ledgerSummary({ date: "2026-10-29", topics: {}, total: 0 })).toBe("今日已主动 0 次");
  });
});

describe("avatar pack preview", () => {
  it("prefers the idle frame and falls back to the pack fallback", () => {
    expect(previewFrame(makePack("default", { idle: ["idle_0.png"], thinking: ["t.png"] }))).toBe(
      "idle_0.png",
    );
    expect(previewFrame(makePack("default", { thinking: ["t.png"] }))).toBe("t.png");
  });

  it("returns null when the pack has no frame to show", () => {
    expect(previewFrame(makePack("default", { sleeping: [] }))).toBeNull();
  });

  it("counts states and frames for the settings list", () => {
    expect(
      packStatesText(makePack("default", { idle: ["a.png", "b.png"], thinking: ["c.png"] })),
    ).toBe("2 组状态 / 3 帧");
    expect(packStatesText(makePack("default", {}))).toBe("0 组状态 / 0 帧");
  });
});

describe("interaction labels", () => {
  it("covers every kind the backend can report", () => {
    expect(INTERACTION_KINDS).toEqual(["click", "hover", "drag", "menu"]);
    for (const kind of INTERACTION_KINDS) {
      expect(INTERACTION_LABELS[kind]).toBeTruthy();
    }
  });
});
