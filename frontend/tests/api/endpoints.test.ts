import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { createApiClient, type ApiClient } from "../../src/api/client";
import {
  deleteMemory,
  getActiveLlm,
  getHealth,
  getMemory,
  listChatHistory,
  listLlmProviders,
  listMemories,
  reloadLlmConfig,
  sendChat,
  setActiveLlm,
  setMemoryTemporaryMode,
  updateMemory,
} from "../../src/api/endpoints";

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
      operationId: "set_active_llm",
      run: (c) => setActiveLlm(c, { chat: { provider: "deepseek", model: "deepseek-flash" } }),
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
      operationId: "get_memory",
      run: (c) => getMemory(c, "m_20261018_001"),
      template: "/api/memories/{memory_id}",
      expectedPath: "/api/memories/m_20261018_001",
    },
    {
      operationId: "update_memory",
      run: (c) => updateMemory(c, "m_20261018_001", { content: "拿铁" }),
      template: "/api/memories/{memory_id}",
      expectedPath: "/api/memories/m_20261018_001",
    },
    {
      operationId: "delete_memory",
      run: (c) => deleteMemory(c, "m_20261018_001"),
      template: "/api/memories/{memory_id}",
      expectedPath: "/api/memories/m_20261018_001",
    },
    {
      operationId: "set_memory_temporary_mode",
      run: (c) => setMemoryTemporaryMode(c, true),
      template: "/api/memories/temporary-mode",
      expectedPath: "/api/memories/temporary-mode",
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
    await listMemories(client, { category: "preference", date: "2026-10-18" });
    const url = new URL(calls[0]?.url ?? "");
    expect(url.searchParams.get("category")).toBe("preference");
    expect(url.searchParams.get("date")).toBe("2026-10-18");
  });

  it("encodes memory ids in the path", async () => {
    const { client, calls } = recordingClient();
    await getMemory(client, "a/b c");
    expect(calls[0]?.url).toBe(`${BASE}/api/memories/a%2Fb%20c`);
  });

  it("sends PATCH bodies and the temporary mode flag as JSON", async () => {
    const { client, calls } = recordingClient();
    await updateMemory(client, "m_20261018_001", { title: "咖啡", importance: 4 });
    expect(calls[0]?.init?.method).toBe("PATCH");
    expect(bodyOf(calls[0]?.init)).toEqual({ title: "咖啡", importance: 4 });

    await setMemoryTemporaryMode(client, false);
    expect(bodyOf(calls[1]?.init)).toEqual({ enabled: false });
  });

  it("sends the model selection as the PUT body", async () => {
    const { client, calls } = recordingClient();
    await setActiveLlm(client, { consolidate: { provider: "deepseek", model: "deepseek-flash" } });
    expect(calls[0]?.init?.method).toBe("PUT");
    expect(bodyOf(calls[0]?.init)).toEqual({
      consolidate: { provider: "deepseek", model: "deepseek-flash" },
    });
  });

  it("does not send a body for GET or DELETE requests", async () => {
    const { client, calls } = recordingClient();
    await getActiveLlm(client);
    await deleteMemory(client, "m_20261018_001");
    expect(calls[0]?.init?.body).toBeUndefined();
    expect(calls[1]?.init?.body).toBeUndefined();
    expect(new Headers(calls[0]?.init?.headers).has("Content-Type")).toBe(false);
  });
});
