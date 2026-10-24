import type { BackendInfo } from "../../electron/shared/ipc";

export const TOKEN_HEADER = "X-Pet-Token";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export interface ApiClient {
  readonly baseUrl: string;
  readonly token: string;
  readonly fetch: typeof fetch;
}

export function createApiClient(info: BackendInfo, fetchImpl: typeof fetch = fetch): ApiClient {
  // 包装一层：直接保存 window.fetch 后以 client.fetch(...) 调用时，this 会变成 client，浏览器抛出 Illegal invocation。
  const callFetch: typeof fetch = (input, init) => fetchImpl(input, init);
  return {
    baseUrl: `http://127.0.0.1:${info.port}`,
    token: info.token,
    fetch: callFetch,
  };
}

type ErrorBody = { error?: { code?: unknown; message?: unknown } };

async function readBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function request<T>(client: ApiClient, method: string, path: string): Promise<T> {
  const response = await client.fetch(`${client.baseUrl}${path}`, {
    method,
    headers: { [TOKEN_HEADER]: client.token },
  });
  const body = await readBody(response);
  if (!response.ok) {
    const error = (body as ErrorBody | null)?.error;
    const code = typeof error?.code === "string" ? error.code : "http_error";
    const message =
      typeof error?.message === "string" ? error.message : response.statusText || "请求失败";
    throw new ApiError(response.status, code, message);
  }
  return body as T;
}
