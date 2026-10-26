export const TOKEN_HEADER = "X-Pet-Token";

export interface BackendInfo {
  port: number;
  token: string;
}

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

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  body?: unknown;
  query?: Record<string, QueryValue>;
}

export function createApiClient(info: BackendInfo, fetchImpl: typeof fetch = fetch): ApiClient {
  // 包装一层：直接保存 window.fetch 后以 client.fetch(...) 调用时，this 会变成 client，浏览器会抛出 Illegal invocation。
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

export function buildUrl(
  client: ApiClient,
  path: string,
  query?: Record<string, QueryValue>,
): string {
  const url = new URL(path, client.baseUrl);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export async function request<T>(
  client: ApiClient,
  method: string,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { [TOKEN_HEADER]: client.token };
  let body: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const response = await client.fetch(buildUrl(client, path, options.query), {
    method,
    headers,
    body,
  });
  const payload = await readBody(response);
  if (!response.ok) {
    const error = (payload as ErrorBody | null)?.error;
    const code = typeof error?.code === "string" ? error.code : "http_error";
    const message =
      typeof error?.message === "string" ? error.message : response.statusText || "请求失败";
    throw new ApiError(response.status, code, message);
  }
  return payload as T;
}
