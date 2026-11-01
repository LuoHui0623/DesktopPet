import type { ApiClient } from "./client";
import type { PetState, ServerEvent } from "./types";

export const WS_SUBPROTOCOL = "pet.v1";
export const WS_PATH = "/ws";

const EVENT_TYPES = new Set([
  "status",
  "chat.chunk",
  "chat.done",
  "approval.request",
  "proactive.message",
  "usage.call.started",
  "usage.call.finished",
  "usage.turn.started",
  "usage.turn.finished",
  "usage.panel.snapshot",
]);
const PET_STATES = new Set<PetState>(["idle", "thinking", "speaking", "executing", "sleeping"]);

export interface SocketLike {
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  close(code?: number): void;
}

export type SocketFactory = (url: string, protocols: string[]) => SocketLike;

export interface Backoff {
  initialMs: number;
  maxMs: number;
}

export interface EventStreamOptions {
  client: ApiClient;
  onEvent: (event: ServerEvent) => void;
  onConnectionChange?: (connected: boolean) => void;
  createSocket?: SocketFactory;
  backoff?: Backoff;
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export interface EventStream {
  readonly connected: boolean;
  close(): void;
}

export const DEFAULT_BACKOFF: Backoff = { initialMs: 500, maxMs: 10_000 };

export function eventsUrl(baseUrl: string): string {
  return `${baseUrl.replace(/^http/, "ws")}${WS_PATH}`;
}

export function parseServerEvent(data: unknown): ServerEvent | null {
  if (typeof data !== "string") return null;
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof payload !== "object" || payload === null) return null;
  const type = (payload as { type?: unknown }).type;
  if (typeof type !== "string" || !EVENT_TYPES.has(type)) return null;
  if (type === "status") {
    const state = (payload as { state?: unknown }).state;
    if (typeof state !== "string" || !PET_STATES.has(state as PetState)) return null;
  }
  return payload as ServerEvent;
}

export function connectEvents(options: EventStreamOptions): EventStream {
  const { client, onEvent, onConnectionChange } = options;
  const createSocket: SocketFactory =
    options.createSocket ?? ((url, protocols) => new WebSocket(url, protocols) as SocketLike);
  const backoff = options.backoff ?? DEFAULT_BACKOFF;
  const setTimer = options.setTimer ?? ((callback, ms) => setTimeout(callback, ms));
  const clearTimer =
    options.clearTimer ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));

  let socket: SocketLike | null = null;
  let timer: unknown = null;
  let attempt = 0;
  let closed = false;
  let connected = false;

  const setConnected = (value: boolean) => {
    if (connected === value) return;
    connected = value;
    onConnectionChange?.(value);
  };

  const scheduleReconnect = () => {
    const delay = Math.min(backoff.initialMs * 2 ** attempt, backoff.maxMs);
    attempt += 1;
    timer = setTimer(() => {
      timer = null;
      if (!closed) open();
    }, delay);
  };

  const open = () => {
    const created = createSocket(eventsUrl(client.baseUrl), [WS_SUBPROTOCOL, client.token]);
    socket = created;
    created.onopen = () => {
      attempt = 0;
      setConnected(true);
    };
    created.onmessage = (message) => {
      const event = parseServerEvent(message.data);
      if (event !== null) onEvent(event);
    };
    created.onerror = () => undefined;
    created.onclose = () => {
      if (socket === created) socket = null;
      setConnected(false);
      // 重连期间不推断宠物状态：状态以服务端下一次 status 事件为准。
      if (!closed) scheduleReconnect();
    };
  };

  open();

  return {
    get connected() {
      return connected;
    },
    close() {
      if (closed) return;
      closed = true;
      if (timer !== null) {
        clearTimer(timer);
        timer = null;
      }
      socket?.close(1000);
      socket = null;
      setConnected(false);
    },
  };
}
