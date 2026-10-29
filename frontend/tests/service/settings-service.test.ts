import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsView } from "../../src/api/types";
import {
  PET_WINDOW_LABEL,
  SETTINGS_CHANGED_EVENT,
  clearPerception,
  listenSettingsChanged,
  loadSchedulerLedger,
  loadSettings,
  notifyPetSettingsChanged,
  saveActiveAvatar,
  saveInteractionConfig,
  savePerceptionConfig,
  saveProactiveConfig,
  type SettingsDomain,
} from "../../src/service/settings.service";

const emitTo = vi.hoisted(() => vi.fn());
const listen = vi.hoisted(() => vi.fn());
const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/event", () => ({ emitTo, listen }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

const view: SettingsView = {
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
  avatar: { active: "default", packs: [] },
  interaction: {
    report_enabled_kinds: ["click"],
    hover_dwell_ms: 300,
    report_interval_ms: 1000,
    reaction_ms: 220,
  },
};

interface Call {
  url: string;
  method: string;
  body: unknown;
}

const calls: Call[] = [];

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  calls.length = 0;
  emitTo.mockReset().mockResolvedValue(undefined);
  listen.mockReset().mockResolvedValue(() => undefined);
  invoke.mockReset().mockResolvedValue({ port: 8765, token: "token-value" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
      calls.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
      });
      if (String(input).endsWith("/api/scheduler/ledger")) {
        return jsonResponse({ date: "2026-10-29", topics: {}, total: 0 });
      }
      if (String(input).endsWith("/api/perception/clear")) {
        return jsonResponse({ cleared: true });
      }
      return jsonResponse(view);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("settings read model", () => {
  it("reads the four groups from one aggregate endpoint", async () => {
    expect(await loadSettings()).toEqual(view);
    expect(calls[0]).toEqual({
      url: "http://127.0.0.1:8765/api/settings",
      method: "GET",
      body: undefined,
    });
  });

  it("reads the proactive ledger for the settings page", async () => {
    expect(await loadSchedulerLedger()).toEqual({
      date: "2026-10-29",
      topics: {},
      total: 0,
    });
  });
});

describe("saving notifies the pet window", () => {
  const cases: { domain: SettingsDomain; save: () => Promise<SettingsView>; path: string }[] = [
    {
      domain: "perception",
      save: () =>
        savePerceptionConfig({
          enabled: false,
          foreground_enabled: true,
          idle_away_minutes: 20,
          sit_minutes: 45,
        }),
      path: "/api/settings/perception",
    },
    {
      domain: "proactive",
      save: () =>
        saveProactiveConfig({
          enabled: false,
          min_interval_minutes: 90,
          max_per_day: 8,
          dnd_start: "23:00",
          dnd_end: "07:30",
        }),
      path: "/api/settings/proactive",
    },
    { domain: "avatar", save: () => saveActiveAvatar("alt"), path: "/api/settings/avatar" },
    {
      domain: "interaction",
      save: () =>
        saveInteractionConfig({
          report_enabled_kinds: ["click", "drag"],
          hover_dwell_ms: 500,
          report_interval_ms: 2000,
          reaction_ms: 400,
        }),
      path: "/api/settings/interaction",
    },
  ];

  for (const testCase of cases) {
    it(`${testCase.domain} writes its group and tells the pet window to reload`, async () => {
      expect(await testCase.save()).toEqual(view);

      const request = calls.find((call) => call.url.endsWith(testCase.path));
      expect(request?.method).toBe("PUT");
      expect(request?.body).toBeDefined();
      expect(emitTo).toHaveBeenCalledWith(PET_WINDOW_LABEL, SETTINGS_CHANGED_EVENT, {
        domain: testCase.domain,
      });
    });
  }

  it("clears the in-memory perception buffer without a reload event", async () => {
    expect(await clearPerception()).toEqual({ cleared: true });
    expect(calls[0]?.method).toBe("POST");
    expect(emitTo).not.toHaveBeenCalled();
  });

  it("still returns the saved view when the event bus is unavailable", async () => {
    emitTo.mockRejectedValue(new Error("not running inside Tauri"));
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    expect(await saveActiveAvatar("default")).toEqual(view);
    expect(errorSpy).toHaveBeenCalled();
  });

  it("swallows notification failures outside Tauri", async () => {
    emitTo.mockRejectedValue(new Error("offline"));
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    await expect(notifyPetSettingsChanged("interaction")).resolves.toBeUndefined();
  });
});

describe("listening for changes", () => {
  it("forwards the domain carried by the event payload", async () => {
    const seen: SettingsDomain[] = [];
    const handlers: ((event: { payload: unknown }) => void)[] = [];
    listen.mockImplementation(
      async (_name: string, next: (event: { payload: unknown }) => void) => {
        handlers.push(next);
        return () => undefined;
      },
    );

    const stop = await listenSettingsChanged((domain) => seen.push(domain));
    handlers[0]?.({ payload: { domain: "avatar" } });
    stop();

    expect(seen).toEqual(["avatar"]);
    expect(listen).toHaveBeenCalledWith(SETTINGS_CHANGED_EVENT, expect.any(Function));
  });
});
