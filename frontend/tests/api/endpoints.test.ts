import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { createApiClient, type ApiClient } from "../../src/api/client";
import {
  clearPerceptionRecords,
  createMemoryEntry,
  decidePerceptionProcess,
  disableMemoryEntry,
  enableMemoryEntry,
  getActiveLlm,
  getInteractionConfig,
  getPerceptionState,
  getSettings,
  getHealth,
  ingestPerception,
  listChatHistory,
  listLlmProviders,
  listMemories,
  listMemoryEntries,
  listMemoryTags,
  mergeMemoryTags,
  previewTagMerge,
  reloadLlmConfig,
  saveStructuredMemory,
  sendChat,
  submitApproval,
  updateMemoryEntry,
  updateSettingsAvatar,
  updateSettingsInteraction,
  updateSettingsModel,
  updateSettingsPerception,
  updateSettingsPersona,
  updateSettingsProactive,
} from "../../src/api/endpoints";
import type { InteractionConfig, PerceptionConfig, SchedulerConfig } from "../../src/api/types";

const FRONTEND_DIR = fileURLToPath(new URL("../..", import.meta.url));
const CONTRACTS_FILE = path.join(FRONTEND_DIR, "src", "api", "api-contracts.json");
const info = { port: 8765, token: "token-value" };

interface Operation {
  operationId: string;
  method: string;
  path: string;
}

const contracts = JSON.parse(readFileSync(CONTRACTS_FILE, "utf-8")) as {
  operations: Operation[];
};

function operation(operationId: string): Operation {
  const found = contracts.operations.find((item) => item.operationId === operationId);
  if (!found) throw new Error(`契约中缺少 ${operationId}`);
  return found;
}

function recordingClient() {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  const client: ApiClient = createApiClient(info, fetchImpl);
  return { client, calls };
}

function bodyOf(init: RequestInit | undefined): unknown {
  return typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
}

const BASE = `http://127.0.0.1:${info.port}`;

const perceptionConfig: PerceptionConfig = {
  enabled: true,
  foreground_enabled: false,
  idle_away_minutes: 20,
  sit_minutes: 45,
};
const proactiveConfig: SchedulerConfig = {
  enabled: false,
  min_interval_minutes: 90,
  max_per_day: 8,
  dnd_start: "23:00",
  dnd_end: "07:30",
};
const interactionConfig: InteractionConfig = {
  report_enabled_kinds: ["click", "drag"],
  hover_dwell_ms: 500,
  report_interval_ms: 2000,
  reaction_ms: 400,
};

describe("endpoint functions match the API contract", () => {
  const cases: {
    operationId: string;
    run: (client: ApiClient) => Promise<unknown>;
    template: string;
    expectedPath: string;
  }[] = [
    { operationId: "health", run: getHealth, template: "/api/health", expectedPath: "/api/health" },
    {
      operationId: "send_chat",
      run: (c) => sendChat(c, "你好"),
      template: "/api/chat",
      expectedPath: "/api/chat",
    },
    {
      operationId: "list_chat_history",
      run: (c) => listChatHistory(c),
      template: "/api/chat/history",
      expectedPath: "/api/chat/history",
    },
    {
      operationId: "list_llm_providers",
      run: listLlmProviders,
      template: "/api/llm/providers",
      expectedPath: "/api/llm/providers",
    },
    {
      operationId: "reload_llm_config",
      run: reloadLlmConfig,
      template: "/api/llm/reload",
      expectedPath: "/api/llm/reload",
    },
    {
      operationId: "get_active_llm",
      run: getActiveLlm,
      template: "/api/llm/active",
      expectedPath: "/api/llm/active",
    },
    {
      operationId: "list_memories",
      run: (c) => listMemories(c),
      template: "/api/memories",
      expectedPath: "/api/memories",
    },
    {
      operationId: "save_structured_memory",
      run: (c) => saveStructuredMemory(c, "## 主人信息\n- 喜欢拿铁"),
      template: "/api/memories/structured",
      expectedPath: "/api/memories/structured",
    },
    {
      operationId: "submit_approval",
      run: (c) => submitApproval(c, { approval_id: "ap_1", approved: true }),
      template: "/api/agent/approvals",
      expectedPath: "/api/agent/approvals",
    },
    {
      operationId: "ingest_perception",
      run: (c) =>
        ingestPerception(c, {
          kind: "idle",
          value: 120_000,
          at: "2026-10-29T10:00:00Z",
        }),
      template: "/api/perception",
      expectedPath: "/api/perception",
    },
    {
      operationId: "get_perception_state",
      run: getPerceptionState,
      template: "/api/perception/state",
      expectedPath: "/api/perception/state",
    },
    {
      operationId: "decide_perception_process",
      run: (c) => decidePerceptionProcess(c, { exe: "code", decision: "allow" }),
      template: "/api/perception/processes/decision",
      expectedPath: "/api/perception/processes/decision",
    },
    {
      operationId: "clear_perception_records",
      run: clearPerceptionRecords,
      template: "/api/perception/clear",
      expectedPath: "/api/perception/clear",
    },
    {
      operationId: "get_interaction_config",
      run: getInteractionConfig,
      template: "/api/interaction/config",
      expectedPath: "/api/interaction/config",
    },
    {
      operationId: "get_settings",
      run: getSettings,
      template: "/api/settings",
      expectedPath: "/api/settings",
    },
    {
      operationId: "update_settings_perception",
      run: (c) => updateSettingsPerception(c, perceptionConfig, 7),
      template: "/api/settings/perception",
      expectedPath: "/api/settings/perception",
    },
    {
      operationId: "update_settings_proactive",
      run: (c) => updateSettingsProactive(c, proactiveConfig, 7),
      template: "/api/settings/proactive",
      expectedPath: "/api/settings/proactive",
    },
    {
      operationId: "update_settings_avatar",
      run: (c) => updateSettingsAvatar(c, "default", 7),
      template: "/api/settings/avatar",
      expectedPath: "/api/settings/avatar",
    },
    {
      operationId: "update_settings_persona",
      run: (c) => updateSettingsPersona(c, "芙芙", 7),
      template: "/api/settings/persona",
      expectedPath: "/api/settings/persona",
    },
    {
      operationId: "update_settings_model",
      run: (c) =>
        updateSettingsModel(c, { chat: { provider: "deepseek", model: "deepseek-flash" } }, 7),
      template: "/api/settings/model",
      expectedPath: "/api/settings/model",
    },
    {
      operationId: "update_settings_interaction",
      run: (c) => updateSettingsInteraction(c, interactionConfig, 7),
      template: "/api/settings/interaction",
      expectedPath: "/api/settings/interaction",
    },
    {
      operationId: "list_memory_entries",
      run: (c) => listMemoryEntries(c),
      template: "/api/memories/entries",
      expectedPath: "/api/memories/entries",
    },
    {
      operationId: "create_memory_entry",
      run: (c) => createMemoryEntry(c, { content: "喜欢拿铁", tags: ["饮食"] }),
      template: "/api/memories/entries",
      expectedPath: "/api/memories/entries",
    },
    {
      operationId: "update_memory_entry",
      run: (c) => updateMemoryEntry(c, 1, { content: "喜欢美式", expected_revision: 2 }),
      template: "/api/memories/entries/{entry_id}",
      expectedPath: "/api/memories/entries/1",
    },
    {
      operationId: "enable_memory_entry",
      run: (c) => enableMemoryEntry(c, 1),
      template: "/api/memories/entries/{entry_id}/enable",
      expectedPath: "/api/memories/entries/1/enable",
    },
    {
      operationId: "disable_memory_entry",
      run: (c) => disableMemoryEntry(c, 1, "已迁入人设"),
      template: "/api/memories/entries/{entry_id}/disable",
      expectedPath: "/api/memories/entries/1/disable",
    },
    {
      operationId: "list_memory_tags",
      run: listMemoryTags,
      template: "/api/memories/tags",
      expectedPath: "/api/memories/tags",
    },
    {
      operationId: "preview_tag_merge",
      run: (c) => previewTagMerge(c, { source: "水果", target: "食物" }),
      template: "/api/memories/tags/merge/preview",
      expectedPath: "/api/memories/tags/merge/preview",
    },
    {
      operationId: "merge_memory_tags",
      run: (c) => mergeMemoryTags(c, { source: "水果", target: "食物" }),
      template: "/api/memories/tags/merge",
      expectedPath: "/api/memories/tags/merge",
    },
  ];

  for (const testCase of cases) {
    it(`${testCase.operationId} uses the contract method and path`, async () => {
      const contract = operation(testCase.operationId);
      expect(contract.path).toBe(testCase.template);
      const { client, calls } = recordingClient();
      await testCase.run(client);
      expect(calls[0]?.init?.method).toBe(contract.method);
      expect(new URL(calls[0]?.url ?? "").pathname).toBe(testCase.expectedPath);
    });
  }
});

describe("request bodies and queries", () => {
  it("sends the chat text as JSON with the token header", async () => {
    const { client, calls } = recordingClient();
    await sendChat(client, "你好");
    expect(calls[0]?.url).toBe(`${BASE}/api/chat`);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(bodyOf(calls[0]?.init)).toEqual({ text: "你好" });
    expect(new Headers(calls[0]?.init?.headers).get("Content-Type")).toBe("application/json");
    expect(new Headers(calls[0]?.init?.headers).get("X-Pet-Token")).toBe("token-value");
  });

  it("puts history filters in the query string and skips empty values", async () => {
    const { client, calls } = recordingClient();
    await listChatHistory(client, { keyword: "咖啡", limit: 20, date_from: "2026-10-18" });
    const url = new URL(calls[0]?.url ?? "");
    expect(url.searchParams.get("keyword")).toBe("咖啡");
    expect(url.searchParams.get("limit")).toBe("20");
    expect(url.searchParams.get("date_from")).toBe("2026-10-18");
    expect(url.searchParams.has("offset")).toBe(false);
    expect(url.searchParams.has("date_to")).toBe(false);
  });

  it("sends memory filters as query parameters", async () => {
    const { client, calls } = recordingClient();
    await listMemories(client, { date: "2026-10-18" });
    const url = new URL(calls[0]?.url ?? "");
    expect(url.searchParams.get("date")).toBe("2026-10-18");
  });

  it("sends the structured memory text as the PUT body", async () => {
    const { client, calls } = recordingClient();
    await saveStructuredMemory(client, "## 主人信息\n- 喜欢拿铁");
    expect(calls[0]?.url).toBe(`${BASE}/api/memories/structured`);
    expect(calls[0]?.init?.method).toBe("PUT");
    expect(bodyOf(calls[0]?.init)).toEqual({ text: "## 主人信息\n- 喜欢拿铁" });
  });

  it("no longer exposes the temporary mode operation or its contract", () => {
    const ids = contracts.operations.map((item) => item.operationId);
    expect(ids).not.toContain("set_memory_temporary_mode");
  });

  it("sends the approval decision as JSON", async () => {
    const { client, calls } = recordingClient();
    await submitApproval(client, { approval_id: "ap_1", approved: false });
    expect(calls[0]?.url).toBe(`${BASE}/api/agent/approvals`);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(bodyOf(calls[0]?.init)).toEqual({ approval_id: "ap_1", approved: false });
  });

  it("sends perception samples as JSON", async () => {
    const { client, calls } = recordingClient();
    const sample = { kind: "foreground" as const, value: "code", at: "2026-10-29T10:00:00Z" };
    await ingestPerception(client, sample);
    expect(calls[0]?.url).toBe(`${BASE}/api/perception`);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(bodyOf(calls[0]?.init)).toEqual(sample);
  });

  it("sends the candidate decision as JSON", async () => {
    const { client, calls } = recordingClient();
    await decidePerceptionProcess(client, { exe: "code", decision: "ignore" });
    expect(calls[0]?.url).toBe(`${BASE}/api/perception/processes/decision`);
    expect(bodyOf(calls[0]?.init)).toEqual({ exe: "code", decision: "ignore" });
  });

  it("sends the model selection with the snapshot version", async () => {
    const { client, calls } = recordingClient();
    await updateSettingsModel(
      client,
      { consolidate: { provider: "deepseek", model: "deepseek-flash" } },
      12,
    );
    expect(calls[0]?.url).toBe(`${BASE}/api/settings/model`);
    expect(calls[0]?.init?.method).toBe("PUT");
    expect(bodyOf(calls[0]?.init)).toEqual({
      consolidate: { provider: "deepseek", model: "deepseek-flash" },
    });
    expect(new Headers(calls[0]?.init?.headers).get("If-Match")).toBe("12");
  });

  it("sends each settings group as the PUT body of the aggregate view", async () => {
    const { client, calls } = recordingClient();
    await updateSettingsPerception(client, perceptionConfig, 7);
    await updateSettingsProactive(client, proactiveConfig, 7);
    await updateSettingsAvatar(client, "alt", 7);
    await updateSettingsPersona(client, "芙芙", 7);
    await updateSettingsInteraction(client, interactionConfig, 7);

    expect(calls.map((call) => call.init?.method)).toEqual(["PUT", "PUT", "PUT", "PUT", "PUT"]);
    expect(bodyOf(calls[0]?.init)).toEqual(perceptionConfig);
    expect(bodyOf(calls[1]?.init)).toEqual(proactiveConfig);
    expect(bodyOf(calls[2]?.init)).toEqual({ id: "alt" });
    expect(bodyOf(calls[3]?.init)).toEqual({ id: "芙芙" });
    expect(bodyOf(calls[4]?.init)).toEqual(interactionConfig);
    for (const call of calls) {
      expect(new Headers(call.init?.headers).get("If-Match")).toBe("7");
    }
  });

  it("does not send a body for GET requests", async () => {
    const { client, calls } = recordingClient();
    await getActiveLlm(client);
    await listMemories(client);
    expect(calls[0]?.init?.body).toBeUndefined();
    expect(calls[1]?.init?.body).toBeUndefined();
    expect(new Headers(calls[0]?.init?.headers).has("Content-Type")).toBe(false);
  });
});

describe("memory entry and tag governance endpoints", () => {
  it("sends the new entry content and tags as JSON", async () => {
    const { client, calls } = recordingClient();
    await createMemoryEntry(client, { content: "喜欢拿铁", tags: ["饮食"] });
    expect(calls[0]?.url).toBe(`${BASE}/api/memories/entries`);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(bodyOf(calls[0]?.init)).toEqual({ content: "喜欢拿铁", tags: ["饮食"] });
  });

  it("sends expected_revision with the update body", async () => {
    const { client, calls } = recordingClient();
    await updateMemoryEntry(client, 7, { tags: ["饮食", "咖啡"], expected_revision: 3 });
    expect(calls[0]?.url).toBe(`${BASE}/api/memories/entries/7`);
    expect(calls[0]?.init?.method).toBe("PUT");
    expect(bodyOf(calls[0]?.init)).toEqual({ tags: ["饮食", "咖啡"], expected_revision: 3 });
  });

  it("sends the disable reason as JSON", async () => {
    const { client, calls } = recordingClient();
    await disableMemoryEntry(client, 7, "已迁入人设");
    expect(calls[0]?.url).toBe(`${BASE}/api/memories/entries/7/disable`);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(bodyOf(calls[0]?.init)).toEqual({ reason: "已迁入人设" });
  });

  it("posts enable without a body", async () => {
    const { client, calls } = recordingClient();
    await enableMemoryEntry(client, 7);
    expect(calls[0]?.url).toBe(`${BASE}/api/memories/entries/7/enable`);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.init?.body).toBeUndefined();
    expect(new Headers(calls[0]?.init?.headers).has("Content-Type")).toBe(false);
  });

  it("puts the entry status filter in the query string", async () => {
    const { client, calls } = recordingClient();
    await listMemoryEntries(client, { status: "disabled" });
    const url = new URL(calls[0]?.url ?? "");
    expect(url.pathname).toBe("/api/memories/entries");
    expect(url.searchParams.get("status")).toBe("disabled");
  });

  it("omits the status filter when listing all entries", async () => {
    const { client, calls } = recordingClient();
    await listMemoryEntries(client);
    const url = new URL(calls[0]?.url ?? "");
    expect(url.searchParams.has("status")).toBe(false);
  });

  it("sends source and target for merge preview and execute", async () => {
    const { client, calls } = recordingClient();
    await previewTagMerge(client, { source: "水果", target: "食物" });
    await mergeMemoryTags(client, { source: "水果", target: "食物" });
    expect(calls[0]?.url).toBe(`${BASE}/api/memories/tags/merge/preview`);
    expect(calls[1]?.url).toBe(`${BASE}/api/memories/tags/merge`);
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[1]?.init?.method).toBe("POST");
    expect(bodyOf(calls[0]?.init)).toEqual({ source: "水果", target: "食物" });
    expect(bodyOf(calls[1]?.init)).toEqual({ source: "水果", target: "食物" });
  });

  it("does not send a body when listing tags", async () => {
    const { client, calls } = recordingClient();
    await listMemoryTags(client);
    expect(calls[0]?.url).toBe(`${BASE}/api/memories/tags`);
    expect(calls[0]?.init?.method).toBe("GET");
    expect(calls[0]?.init?.body).toBeUndefined();
  });
});
