import { describe, expect, it } from "vitest";
import type { PersonaItem } from "../../src/api/types";
import { canActivate, personaStatusText } from "../../src/modules/settings/persona-view";

const base: PersonaItem = {
  id: "小桌",
  name: "小桌",
  available: true,
  reason: null,
  active: false,
};

describe("personaStatusText", () => {
  it("shows the active card as in use, even when it is available", () => {
    expect(personaStatusText({ ...base, active: true })).toBe("使用中");
  });

  it("shows available and unavailable cards", () => {
    expect(personaStatusText(base)).toBe("可用");
    expect(personaStatusText({ ...base, available: false, reason: "缺少必填字段：人设" })).toBe(
      "不可用",
    );
  });
});

describe("canActivate", () => {
  it("allows an available card that is not active yet", () => {
    expect(canActivate(base)).toBe(true);
  });

  it("blocks the active card and unavailable cards", () => {
    expect(canActivate({ ...base, active: true })).toBe(false);
    expect(canActivate({ ...base, available: false })).toBe(false);
  });
});
