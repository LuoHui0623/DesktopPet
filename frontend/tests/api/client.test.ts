import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ApiError, createApiClient } from "../../src/api/client";
import { getHealth } from "../../src/api/endpoints";

const FRONTEND_DIR = fileURLToPath(new URL("../..", import.meta.url));
const CONTRACTS_FILE = path.join(FRONTEND_DIR, "src", "api", "api-contracts.json");
const info = { port: 8765, token: "token-value" };

function fakeFetch(respond: () => Response | Promise<Response>) {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return respond();
  }) as typeof fetch;
  return { fetchImpl, calls };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("api client", () => {
  it("sends the token header and returns the parsed health body", async () => {
    const { fetchImpl, calls } = fakeFetch(() => json(200, { status: "ok" }));
    const client = createApiClient(info, fetchImpl);

    const health = await getHealth(client);

    expect(health).toEqual({ status: "ok" });
    expect(calls[0]?.url).toBe("http://127.0.0.1:8765/api/health");
    expect(calls[0]?.init?.method).toBe("GET");
    expect(new Headers(calls[0]?.init?.headers).get("X-Pet-Token")).toBe("token-value");
  });

  it("maps the unified error body to ApiError", async () => {
    const body = { error: { code: "unauthorized", message: "缺少或无效的 X-Pet-Token" } };
    const { fetchImpl } = fakeFetch(() => json(401, body));
    const client = createApiClient({ ...info, token: "wrong" }, fetchImpl);

    const error = await getHealth(client).catch((exc: unknown) => exc);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).code).toBe("unauthorized");
    expect((error as ApiError).message).toBe("缺少或无效的 X-Pet-Token");
  });

  it("falls back to http_error when the error body is not JSON", async () => {
    const { fetchImpl } = fakeFetch(
      () => new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" }),
    );
    const client = createApiClient(info, fetchImpl);

    const error = (await getHealth(client).catch((exc: unknown) => exc)) as ApiError;

    expect(error.status).toBe(502);
    expect(error.code).toBe("http_error");
    expect(error.message).toBe("Bad Gateway");
  });

  it("does not call fetch with the client as this (browser fetch rejects that)", async () => {
    const fetchImpl = function (this: unknown) {
      if (this !== undefined && this !== globalThis) {
        throw new TypeError("Illegal invocation");
      }
      return Promise.resolve(json(200, { status: "ok" }));
    } as unknown as typeof fetch;
    const client = createApiClient(info, fetchImpl);
    await expect(getHealth(client)).resolves.toEqual({ status: "ok" });
  });

  it("propagates network failures", async () => {
    const { fetchImpl } = fakeFetch(() => {
      throw new TypeError("fetch failed");
    });
    const client = createApiClient(info, fetchImpl);
    await expect(getHealth(client)).rejects.toThrow("fetch failed");
  });
});

describe("api contracts", () => {
  const contracts = JSON.parse(readFileSync(CONTRACTS_FILE, "utf-8")) as {
    operations: { operationId: string; method: string; path: string; responses: object }[];
  };

  it("declares the health operation with a 200 response", () => {
    const health = contracts.operations.find((op) => op.operationId === "health");
    expect(health).toBeDefined();
    expect(health?.method).toBe("GET");
    expect(health?.path).toBe("/api/health");
    expect(Object.keys(health?.responses ?? {})).toContain("200");
  });

  it("uses the contract path for the health request", async () => {
    const health = contracts.operations.find((op) => op.operationId === "health");
    const { fetchImpl, calls } = fakeFetch(() => json(200, { status: "ok" }));
    await getHealth(createApiClient(info, fetchImpl));
    expect(calls[0]?.url).toBe(`http://127.0.0.1:${info.port}${health?.path}`);
  });
});
