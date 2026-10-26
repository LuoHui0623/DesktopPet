import { describe, expect, it } from "vitest";
import { createPetStore } from "../../src/service/stores/pet.store";
import { petStatusLabel } from "../../src/modules/pet/status";

describe("pet status label", () => {
  it.each([
    ["idle", "待机"],
    ["thinking", "思考"],
    ["speaking", "说话"],
    ["executing", "执行中"],
    ["sleeping", "休眠"],
  ] as const)("shows %s as %s while connected", (state, label) => {
    expect(petStatusLabel(true, state)).toBe(label);
  });

  it("shows 未连接 whenever the backend is disconnected, ignoring any state", () => {
    expect(petStatusLabel(false, null)).toBe("未连接");
    expect(petStatusLabel(false, "speaking")).toBe("未连接");
  });

  it("shows 连接中 while connected but no status has arrived yet", () => {
    expect(petStatusLabel(true, null)).toBe("连接中");
  });
});

describe("pet store", () => {
  it("starts disconnected without a status", () => {
    const store = createPetStore();
    expect(store.getState().connected).toBe(false);
    expect(store.getState().status).toBeNull();
  });

  it("applies status events in order", () => {
    const store = createPetStore();
    store.getState().setConnected(true);
    store.getState().applyEvent({ type: "status", state: "thinking" });
    store.getState().applyEvent({ type: "status", state: "speaking" });
    expect(store.getState().status).toBe("speaking");
  });

  it("ignores chat events for the pet status", () => {
    const store = createPetStore();
    store.getState().setConnected(true);
    store.getState().applyEvent({ type: "status", state: "thinking" });
    store.getState().applyEvent({ type: "chat.chunk", message_id: "m1", text: "你好" });
    store.getState().applyEvent({ type: "chat.done", message_id: "m1", text: "你好" });
    expect(store.getState().status).toBe("thinking");
  });

  it("clears the old status when the connection drops", () => {
    const store = createPetStore();
    store.getState().setConnected(true);
    store.getState().applyEvent({ type: "status", state: "executing" });
    store.getState().setConnected(false);
    expect(store.getState().connected).toBe(false);
    expect(store.getState().status).toBeNull();
  });

  it("does not show the old status after reconnecting until the next status event", () => {
    const store = createPetStore();
    store.getState().setConnected(true);
    store.getState().applyEvent({ type: "status", state: "sleeping" });
    store.getState().setConnected(false);
    store.getState().setConnected(true);
    expect(petStatusLabel(store.getState().connected, store.getState().status)).toBe("连接中");
    store.getState().applyEvent({ type: "status", state: "idle" });
    expect(petStatusLabel(store.getState().connected, store.getState().status)).toBe("待机");
  });
});
