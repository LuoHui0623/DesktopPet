import { describe, expect, it } from "vitest";
import { createChatStore } from "../../src/service/stores/chat.store";

function setup() {
  const store = createChatStore();
  const localId = store.getState().beginSend("你好");
  if (localId === null) throw new Error("beginSend failed");
  return { store, localId };
}

describe("chat store: sending", () => {
  it("adds the user message and a pending reply, and marks sending", () => {
    const { store } = setup();
    const messages = store.getState().messages;
    expect(messages.map((m) => [m.role, m.text, m.status])).toEqual([
      ["user", "你好", "done"],
      ["assistant", "", "pending"],
    ]);
    expect(store.getState().sending).toBe(true);
  });

  it("trims input and ignores blank text", () => {
    const store = createChatStore();
    expect(store.getState().beginSend("   ")).toBeNull();
    expect(store.getState().messages).toHaveLength(0);
    store.getState().beginSend("  你好  ");
    expect(store.getState().messages[0]?.text).toBe("你好");
  });

  it("refuses a second send while one is in flight", () => {
    const { store } = setup();
    expect(store.getState().beginSend("再来一次")).toBeNull();
    expect(store.getState().messages).toHaveLength(2);
  });
});

describe("chat store: streaming", () => {
  it("binds the pending reply to the first chunk id and appends in order", () => {
    const { store } = setup();
    store.getState().applyEvent({ type: "chat.chunk", message_id: "m1", text: "你好" });
    store.getState().applyEvent({ type: "chat.chunk", message_id: "m1", text: "呀" });
    const reply = store.getState().messages[1];
    expect(reply?.text).toBe("你好呀");
    expect(reply?.status).toBe("streaming");
    expect(reply?.messageId).toBe("m1");
  });

  it("does not duplicate text when the final reply arrives after the stream", () => {
    const { store, localId } = setup();
    store.getState().applyEvent({ type: "chat.chunk", message_id: "m1", text: "你好" });
    store
      .getState()
      .applyEvent({ type: "chat.done", sources: [], message_id: "m1", text: "你好呀" });
    store
      .getState()
      .completeSend(localId, { message_id: "m1", text: "你好呀", recorded: true, sources: [] });
    const reply = store.getState().messages[1];
    expect(reply?.text).toBe("你好呀");
    expect(reply?.status).toBe("done");
    expect(store.getState().sending).toBe(false);
  });

  it("ignores chunks that arrive after the reply is complete", () => {
    const { store, localId } = setup();
    store
      .getState()
      .completeSend(localId, { message_id: "m1", text: "你好呀", recorded: true, sources: [] });
    store.getState().applyEvent({ type: "chat.chunk", message_id: "m1", text: "你好" });
    expect(store.getState().messages[1]?.text).toBe("你好呀");
  });

  it("keeps a single copy when done arrives before the HTTP reply", () => {
    const { store, localId } = setup();
    store.getState().applyEvent({ type: "chat.chunk", message_id: "m1", text: "你" });
    store.getState().applyEvent({ type: "chat.done", sources: [], message_id: "m1", text: "你好" });
    store
      .getState()
      .completeSend(localId, { message_id: "m1", text: "你好", recorded: true, sources: [] });
    expect(store.getState().messages[1]?.text).toBe("你好");
  });

  it("ignores events for unknown messages when nothing is pending", () => {
    const store = createChatStore();
    store.getState().applyEvent({ type: "chat.chunk", message_id: "x", text: "游离" });
    store.getState().applyEvent({ type: "chat.done", sources: [], message_id: "x", text: "游离" });
    expect(store.getState().messages).toHaveLength(0);
  });

  it("ignores status events", () => {
    const { store } = setup();
    store.getState().applyEvent({ type: "status", state: "thinking" });
    expect(store.getState().messages[1]?.status).toBe("pending");
  });
});

describe("chat store: failure", () => {
  it("shows the error text instead of a blank reply and releases sending", () => {
    const { store, localId } = setup();
    store.getState().failSend(localId, "模型调用失败：超时");
    const reply = store.getState().messages[1];
    expect(reply?.status).toBe("error");
    expect(reply?.text).toBe("模型调用失败：超时");
    expect(store.getState().sending).toBe(false);
  });

  it("allows sending again after a failure", () => {
    const { store, localId } = setup();
    store.getState().failSend(localId, "失败");
    expect(store.getState().beginSend("重试")).not.toBeNull();
  });
});

describe("chat store: sources", () => {
  const source = {
    id: "m_20261018_001",
    kind: "结构化记忆",
    category: "preference",
    date: "2026-10-18",
    text: "喜欢美式咖啡，不加糖",
  };

  it("attaches the sources that come with the done event", () => {
    const { store } = setup();
    store.getState().applyEvent({ type: "chat.chunk", message_id: "m1", text: "你上次说过" });
    store.getState().applyEvent({
      type: "chat.done",
      message_id: "m1",
      text: "你上次说过喜欢美式",
      sources: [source],
    });
    expect(store.getState().messages[1]?.sources).toEqual([source]);
  });

  it("keeps the sources from the HTTP reply when the event arrives later", () => {
    const { store, localId } = setup();
    store.getState().completeSend(localId, {
      message_id: "m1",
      text: "你上次说过喜欢美式",
      recorded: true,
      sources: [source],
    });
    store.getState().applyEvent({
      type: "chat.done",
      message_id: "m1",
      text: "你上次说过喜欢美式",
      sources: [source],
    });
    expect(store.getState().messages[1]?.sources).toEqual([source]);
  });

  it("has no sources on user messages or failed replies", () => {
    const { store, localId } = setup();
    store.getState().failSend(localId, "失败");
    expect(store.getState().messages.map((message) => message.sources)).toEqual([[], []]);
  });
});
