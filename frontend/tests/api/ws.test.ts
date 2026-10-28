import { describe, expect, it } from "vitest";
import { createApiClient } from "../../src/api/client";
import { connectEvents, eventsUrl, parseServerEvent, WS_SUBPROTOCOL } from "../../src/api/ws";
import type { ServerEvent } from "../../src/api/types";

const info = { port: 8765, token: "token-value" };

class FakeSocket {
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  readonly closedWith: number[] = [];

  constructor(
    readonly url: string,
    readonly protocols: string[],
  ) {}

  close(code?: number): void {
    this.closedWith.push(code ?? 1000);
  }

  open(): void {
    this.onopen?.({});
  }

  receive(data: unknown): void {
    this.onmessage?.({ data });
  }

  drop(code = 1006): void {
    this.onclose?.({ code });
  }
}

function makeTimers() {
  const pending: { id: number; callback: () => void; ms: number }[] = [];
  let nextId = 1;
  return {
    pending,
    setTimer(callback: () => void, ms: number): number {
      const id = nextId;
      nextId += 1;
      pending.push({ id, callback, ms });
      return id;
    },
    clearTimer(handle: unknown): void {
      const index = pending.findIndex((timer) => timer.id === handle);
      if (index >= 0) pending.splice(index, 1);
    },
    fire(): number | undefined {
      const next = pending.shift();
      next?.callback();
      return next?.ms;
    },
  };
}

function setup() {
  const sockets: FakeSocket[] = [];
  const timers = makeTimers();
  const events: ServerEvent[] = [];
  const connections: boolean[] = [];
  const stream = connectEvents({
    client: createApiClient(info),
    onEvent: (event) => events.push(event),
    onConnectionChange: (connected) => connections.push(connected),
    createSocket: (url, protocols) => {
      const socket = new FakeSocket(url, protocols);
      sockets.push(socket);
      return socket;
    },
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
    backoff: { initialMs: 500, maxMs: 4000 },
  });
  return { stream, sockets, timers, events, connections };
}

describe("event stream connection", () => {
  it("builds the websocket url from the backend base url", () => {
    expect(eventsUrl("http://127.0.0.1:8765")).toBe("ws://127.0.0.1:8765/ws");
  });

  it("passes the token as a subprotocol, never in the url", () => {
    const { sockets } = setup();
    expect(sockets[0]?.url).toBe("ws://127.0.0.1:8765/ws");
    expect(sockets[0]?.url).not.toContain(info.token);
    expect(sockets[0]?.protocols).toEqual([WS_SUBPROTOCOL, info.token]);
  });

  it("reports connection changes", () => {
    const { sockets, connections, stream } = setup();
    sockets[0]?.open();
    expect(stream.connected).toBe(true);
    sockets[0]?.drop();
    expect(stream.connected).toBe(false);
    expect(connections).toEqual([true, false]);
  });

  it("delivers events in the order the server sent them", () => {
    const { sockets, events } = setup();
    const socket = sockets[0];
    socket?.open();
    socket?.receive(JSON.stringify({ type: "status", state: "thinking" }));
    socket?.receive(JSON.stringify({ type: "chat.chunk", message_id: "m1", text: "你好。" }));
    socket?.receive(JSON.stringify({ type: "chat.done", message_id: "m1", text: "你好。" }));
    expect(events).toEqual([
      { type: "status", state: "thinking" },
      { type: "chat.chunk", message_id: "m1", text: "你好。" },
      { type: "chat.done", message_id: "m1", text: "你好。" },
    ]);
  });

  it("ignores malformed, unknown, and invalid events", () => {
    const { sockets, events } = setup();
    const socket = sockets[0];
    socket?.open();
    socket?.receive("not json");
    socket?.receive(JSON.stringify({ type: "something.else" }));
    socket?.receive(JSON.stringify({ type: "status", state: "dancing" }));
    socket?.receive(42);
    expect(events).toEqual([]);
  });

  it("reconnects after a drop with the initial delay", () => {
    const { sockets, timers } = setup();
    sockets[0]?.open();
    sockets[0]?.drop();
    expect(timers.pending.map((timer) => timer.ms)).toEqual([500]);
    timers.fire();
    expect(sockets).toHaveLength(2);
    expect(sockets[1]?.url).toBe("ws://127.0.0.1:8765/ws");
  });

  it("doubles the delay on repeated failures and caps it", () => {
    const { sockets, timers } = setup();
    const delays: (number | undefined)[] = [];
    sockets[0]?.drop();
    for (let attempt = 0; attempt < 5; attempt += 1) {
      delays.push(timers.pending[0]?.ms);
      timers.fire();
      sockets[sockets.length - 1]?.drop();
    }
    expect(delays).toEqual([500, 1000, 2000, 4000, 4000]);
  });

  it("resets the delay after a successful reconnect", () => {
    const { sockets, timers } = setup();
    sockets[0]?.drop();
    timers.fire();
    sockets[1]?.drop();
    expect(timers.pending[0]?.ms).toBe(1000);
    timers.fire();
    sockets[2]?.open();
    sockets[2]?.drop();
    expect(timers.pending[0]?.ms).toBe(500);
  });

  it("does not invent a status while reconnecting", () => {
    const { sockets, timers, events } = setup();
    sockets[0]?.open();
    sockets[0]?.receive(JSON.stringify({ type: "status", state: "speaking" }));
    sockets[0]?.drop();
    timers.fire();
    sockets[1]?.open();
    expect(events).toEqual([{ type: "status", state: "speaking" }]);
  });

  it("stops reconnecting after close()", () => {
    const { sockets, timers, stream } = setup();
    sockets[0]?.open();
    sockets[0]?.drop();
    stream.close();
    expect(timers.pending).toEqual([]);
    expect(timers.fire()).toBeUndefined();
    expect(sockets).toHaveLength(1);
  });

  it("closes the live socket without reconnecting", () => {
    const { sockets, timers, stream } = setup();
    sockets[0]?.open();
    stream.close();
    expect(sockets[0]?.closedWith).toEqual([1000]);
    sockets[0]?.drop(1000);
    expect(timers.pending).toEqual([]);
    expect(stream.connected).toBe(false);
  });

  it("parses only known server events", () => {
    expect(parseServerEvent(JSON.stringify({ type: "status", state: "idle" }))).toEqual({
      type: "status",
      state: "idle",
    });
    expect(parseServerEvent(null)).toBeNull();
    expect(parseServerEvent(JSON.stringify([]))).toBeNull();
  });

  it("keeps approval requests instead of dropping them", () => {
    const payload = {
      type: "approval.request",
      plan_id: "plan_1",
      items: [
        {
          approval_id: "ap_1",
          run_id: "r_1",
          capability: "记忆写入",
          task: "记住用户喜欢美式咖啡",
          content: "写入一条偏好记忆",
          tools: ["memory_create"],
        },
      ],
    };
    expect(parseServerEvent(JSON.stringify(payload))).toEqual(payload);
  });
});
