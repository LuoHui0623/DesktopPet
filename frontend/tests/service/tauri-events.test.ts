import { describe, expect, it } from "vitest";
import { MENU_ACTION_EVENT } from "../../src/service/menu.service";
import {
  CANDIDATE_EVENT,
  PERCEPTION_CANDIDATE_EVENT,
  PERCEPTION_SAMPLE_EVENT,
  SAMPLE_EVENT,
} from "../../src/service/perception.service";
import { SETTINGS_CHANGED_EVENT } from "../../src/service/settings.service";
import { TAURI_EVENT_NAME_PATTERN } from "../../src/service/tauri-events";

const NAMES: [string, string][] = [
  ["SETTINGS_CHANGED_EVENT", SETTINGS_CHANGED_EVENT],
  ["MENU_ACTION_EVENT", MENU_ACTION_EVENT],
  ["SAMPLE_EVENT", SAMPLE_EVENT],
  ["CANDIDATE_EVENT", CANDIDATE_EVENT],
];

describe("tauri event names", () => {
  it("only use characters tauri accepts", () => {
    for (const [symbol, name] of NAMES) {
      expect(name, `${symbol} 事件名`).toMatch(TAURI_EVENT_NAME_PATTERN);
    }
  });

  it("match the constants declared in the rust shell", () => {
    expect(SAMPLE_EVENT).toBe(PERCEPTION_SAMPLE_EVENT);
    expect(CANDIDATE_EVENT).toBe(PERCEPTION_CANDIDATE_EVENT);
    expect(SAMPLE_EVENT).toBe("perception-sample");
    expect(CANDIDATE_EVENT).toBe("perception-candidate");
    expect(MENU_ACTION_EVENT).toBe("menu-action");
    expect(SETTINGS_CHANGED_EVENT).toBe("settings-changed");
  });
});
