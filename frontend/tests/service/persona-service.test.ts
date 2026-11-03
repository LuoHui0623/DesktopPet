import { beforeEach, describe, expect, it, vi } from "vitest";
import { PET_WINDOW_LABEL, SETTINGS_CHANGED_EVENT } from "../../src/service/settings.service";
import { switchPersona } from "../../src/service/persona.service";

const emitTo = vi.hoisted(() => vi.fn());
const getApiClient = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/event", () => ({ emitTo, listen: vi.fn() }));
vi.mock("../../src/service/api-client", () => ({ getApiClient }));

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function clientWith(response: Response) {
  return {
    baseUrl: "http://127.0.0.1:4242",
    token: "token",
    fetch: vi.fn(async () => response),
  };
}

beforeEach(() => {
  emitTo.mockReset().mockResolvedValue(undefined);
  getApiClient.mockReset();
});

describe("switchPersona", () => {
  it("notifies the pet window that the avatar group may have changed", async () => {
    const body = { active: "芙芙", personas: [] };
    getApiClient.mockResolvedValue(clientWith(jsonResponse(body)));

    await expect(switchPersona("芙芙")).resolves.toEqual(body);

    expect(emitTo).toHaveBeenCalledWith(PET_WINDOW_LABEL, SETTINGS_CHANGED_EVENT, {
      domain: "persona",
    });
  });

  it("keeps the current avatar when the switch fails", async () => {
    getApiClient.mockResolvedValue(
      clientWith(jsonResponse({ error: { code: "persona_unavailable" } }, 422)),
    );

    await expect(switchPersona("坏了")).rejects.toBeTruthy();
    expect(emitTo).not.toHaveBeenCalled();
  });
});
