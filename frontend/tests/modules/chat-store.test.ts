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

describe("chat store: approvals", () => {
  const item = {
    approval_id: "ap_1",
    run_id: "r_1",
    topic: "记忆写入",
    task: "记住用户喜欢美式咖啡",
    content: "将写入一条偏好记忆：喜欢美式咖啡，不加糖",
    tools: ["memory_create"],
    persona_id: null,
    persona_version: null,
    main_task_id: null,
    subtask_id: null,
    source: null,
    previews: [],
    model: null,
  };

  it("stores the drafts pushed by the approval request event", () => {
    const store = createChatStore();
    store.getState().applyEvent({ type: "approval.request", plan_id: "plan_1", items: [item] });
    expect(store.getState().approvals).toEqual([{ ...item, planId: "plan_1" }]);
  });

  it("marks an approval as in flight and drops it once resolved", () => {
    const store = createChatStore();
    store.getState().applyEvent({ type: "approval.request", plan_id: "plan_1", items: [item] });
    expect(store.getState().beginApproval("ap_1")).toBe(true);
    expect(store.getState().approvalSubmitting).toBe("ap_1");
    store.getState().completeApproval("ap_1");
    expect(store.getState().approvals).toEqual([]);
    expect(store.getState().approvalSubmitting).toBeNull();
  });

  it("ignores unknown ids and a second submission while one is in flight", () => {
    const store = createChatStore();
    store.getState().applyEvent({
      type: "approval.request",
      plan_id: "plan_1",
      items: [item, { ...item, approval_id: "ap_2" }],
    });
    expect(store.getState().beginApproval("ap_unknown")).toBe(false);
    expect(store.getState().beginApproval("ap_1")).toBe(true);
    expect(store.getState().beginApproval("ap_2")).toBe(false);
  });

  it("keeps the card and reports the error when the submission fails", () => {
    const store = createChatStore();
    store.getState().applyEvent({ type: "approval.request", plan_id: "plan_1", items: [item] });
    store.getState().beginApproval("ap_1");
    store.getState().failApproval("ap_1", "审批已关闭");
    expect(store.getState().approvals).toHaveLength(1);
    expect(store.getState().approvalSubmitting).toBeNull();
    expect(store.getState().approvalError).toBe("审批已关闭");
  });

  it("keeps earlier tickets when another approval request arrives", () => {
    const store = createChatStore();
    store.getState().applyEvent({ type: "approval.request", plan_id: "plan_1", items: [item] });
    store.getState().applyEvent({
      type: "approval.request",
      plan_id: "plan_2",
      items: [{ ...item, approval_id: "ap_9", topic: "记忆写入" }],
    });
    expect(store.getState().approvals.map((entry) => entry.approval_id)).toEqual(["ap_1", "ap_9"]);
    expect(store.getState().approvals.map((entry) => entry.planId)).toEqual(["plan_1", "plan_2"]);
  });

  it("updates a repeated approval id in place without duplicating the card", () => {
    const store = createChatStore();
    store.getState().applyEvent({ type: "approval.request", plan_id: "plan_1", items: [item] });
    store.getState().applyEvent({
      type: "approval.request",
      plan_id: "plan_1",
      items: [{ ...item, topic: "读取：world/笔记.md", action: "read", resource: "world" }],
    });
    expect(store.getState().approvals).toHaveLength(1);
    expect(store.getState().approvals[0]?.topic).toBe("读取：world/笔记.md");
    expect(store.getState().approvals[0]?.action).toBe("read");
  });

  it("merges one card per read call while a long task keeps several tickets open", () => {
    const store = createChatStore();
    store.getState().applyEvent({
      type: "approval.request",
      plan_id: "plan_1",
      items: [
        { ...item, approval_id: "read:1", action: "index", resource: "world" },
        { ...item, approval_id: "read:2", action: "read", resource: "world", path: "笔记.md" },
      ],
    });
    store.getState().applyEvent({
      type: "approval.request",
      plan_id: "plan_1",
      items: [{ ...item, approval_id: "read:3", action: "grep", resource: "structured" }],
    });
    expect(store.getState().approvals.map((entry) => entry.approval_id)).toEqual([
      "read:1",
      "read:2",
      "read:3",
    ]);
  });
});

describe("chat store: sources", () => {
  const source = {
    id: "structured",
    kind: "结构化记忆",
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

describe("chat store: several utterances in one turn", () => {
  const tea = {
    id: "2026-10-17",
    kind: "每日对话",
    date: "2026-10-17",
    text: "昨天聊了茶",
  };
  const AT = "2026-10-29T10:00:07+08:00";
  const items = [
    { message_id: "m1a", text: "第一句。", at: AT, sources: [] },
    { message_id: "m1b", text: "第二句！", at: AT, sources: [tea] },
    { message_id: "m1c", text: "第三句。", at: AT, sources: [] },
  ];

  it("turns one pending reply into ordered assistant messages", () => {
    const { store } = setup();
    store.getState().applyEvent({
      type: "chat.done",
      message_id: "m1",
      text: "第一句。\n第二句！\n第三句。",
      sources: [],
      messages: items,
    });
    expect(store.getState().messages.map((message) => [message.role, message.text])).toEqual([
      ["user", "你好"],
      ["assistant", "第一句。"],
      ["assistant", "第二句！"],
      ["assistant", "第三句。"],
    ]);
    expect(
      store
        .getState()
        .messages.slice(1)
        .map((message) => message.messageId),
    ).toEqual(["m1a", "m1b", "m1c"]);
    expect(
      store
        .getState()
        .messages.slice(1)
        .map((message) => message.status),
    ).toEqual(["done", "done", "done"]);
  });

  it("gives every message its own time and sources", () => {
    const { store } = setup();
    store.getState().applyEvent({
      type: "chat.done",
      message_id: "m1",
      text: "第一句。\n第二句！",
      sources: [tea],
      messages: items.slice(0, 2),
    });
    const [, first, second] = store.getState().messages;
    expect(first?.at).toBe(AT);
    expect(second?.at).toBe(AT);
    expect(first?.sources).toEqual([]);
    expect(second?.sources).toEqual([tea]);
  });

  it("uses the HTTP reply when the event arrives later", () => {
    const { store, localId } = setup();
    store.getState().completeSend(localId, {
      message_id: "m1",
      text: "第一句。\n第二句！\n第三句。",
      recorded: true,
      sources: [],
      messages: items,
    });
    store.getState().applyEvent({
      type: "chat.done",
      message_id: "m1",
      text: "第一句。\n第二句！\n第三句。",
      sources: [],
      messages: items,
    });
    expect(store.getState().messages.map((message) => message.text)).toEqual([
      "你好",
      "第一句。",
      "第二句！",
      "第三句。",
    ]);
    expect(store.getState().sending).toBe(false);
    expect(store.getState().pendingLocalId).toBeNull();
  });

  it("does not duplicate the turn when the HTTP reply arrives after the event", () => {
    const { store, localId } = setup();
    store.getState().applyEvent({
      type: "chat.done",
      message_id: "m1",
      text: "第一句。\n第二句！",
      sources: [],
      messages: items.slice(0, 2),
    });
    store.getState().completeSend(localId, {
      message_id: "m1",
      text: "第一句。\n第二句！",
      recorded: true,
      sources: [],
      messages: items.slice(0, 2),
    });
    expect(store.getState().messages.map((message) => message.text)).toEqual([
      "你好",
      "第一句。",
      "第二句！",
    ]);
    expect(store.getState().sending).toBe(false);
  });

  it("replaces the streamed text of the placeholder with the finished messages", () => {
    const { store } = setup();
    store.getState().applyEvent({ type: "chat.chunk", message_id: "m1", text: "第一" });
    store.getState().applyEvent({
      type: "chat.done",
      message_id: "m1",
      text: "第一句。\n第二句！",
      sources: [],
      messages: items.slice(0, 2),
    });
    expect(store.getState().messages.map((message) => message.text)).toEqual([
      "你好",
      "第一句。",
      "第二句！",
    ]);
  });

  it("shows a reply without messages as one message", () => {
    const { store, localId } = setup();
    store.getState().completeSend(localId, {
      message_id: "m1",
      text: "第一句。\n第二句！",
      recorded: true,
      sources: [],
    });
    expect(store.getState().messages.map((message) => message.text)).toEqual([
      "你好",
      "第一句。\n第二句！",
    ]);
    expect(store.getState().messages[1]?.at).toBeNull();
  });

  it("stamps the user message with the local submit time", () => {
    const { store } = setup();
    const at = store.getState().messages[0]?.at;
    expect(typeof at).toBe("string");
    expect(Number.isNaN(Date.parse(at as string))).toBe(false);
  });
});

describe("chat store: notices", () => {
  it("starts empty and stores the notices from the HTTP reply", () => {
    const { store, localId } = setup();
    expect(store.getState().notices).toEqual([]);
    store.getState().completeSend(localId, {
      message_id: "m1",
      text: "你好",
      recorded: true,
      sources: [],
      notices: ["memory_unloaded"],
    });
    expect(store.getState().notices).toEqual(["memory_unloaded"]);
  });

  it("stores notices carried by the done event", () => {
    const { store } = setup();
    store.getState().applyEvent({
      type: "chat.done",
      message_id: "m1",
      text: "你好",
      sources: [],
      notices: ["memory_unloaded"],
    });
    expect(store.getState().notices).toEqual(["memory_unloaded"]);
  });

  it("clears the previous notices when the next turn starts", () => {
    const { store, localId } = setup();
    store.getState().completeSend(localId, {
      message_id: "m1",
      text: "你好",
      recorded: true,
      sources: [],
      notices: ["memory_unloaded"],
    });
    expect(store.getState().beginSend("再来一次")).not.toBeNull();
    expect(store.getState().notices).toEqual([]);
  });
});
