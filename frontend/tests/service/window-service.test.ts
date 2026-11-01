import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  OPEN_CHAT_WINDOW_COMMAND,
  OPEN_SETTINGS_WINDOW_COMMAND,
  OPEN_STATUS_WINDOW_COMMAND,
  openChatWindow,
  openSettingsWindow,
  openStatusWindow,
} from "../../src/service/window.service";

const invoke = vi.hoisted(() => vi.fn());

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

beforeEach(() => {
  invoke.mockReset();
  invoke.mockResolvedValue(undefined);
});

describe("window service", () => {
  it("opens the chat window without history by default", async () => {
    await openChatWindow();

    expect(invoke).toHaveBeenCalledWith(OPEN_CHAT_WINDOW_COMMAND, { history: false });
  });

  it("forwards the history flag to the chat window command", async () => {
    await openChatWindow({ history: true });

    expect(invoke).toHaveBeenCalledWith(OPEN_CHAT_WINDOW_COMMAND, { history: true });
  });

  it("opens the settings window", async () => {
    await openSettingsWindow();

    expect(invoke).toHaveBeenCalledWith(OPEN_SETTINGS_WINDOW_COMMAND);
  });

  it("opens the status window", async () => {
    await openStatusWindow();

    expect(invoke).toHaveBeenCalledWith(OPEN_STATUS_WINDOW_COMMAND);
  });

  // 回归点：命令失败必须能被调用方观测到。若被静默吞掉，PetWindow 的 catch 就永远不触发。
  it("surfaces command failures to the caller", async () => {
    invoke.mockRejectedValue(new Error("deadlock"));

    await expect(openChatWindow()).rejects.toThrow("deadlock");
    await expect(openSettingsWindow()).rejects.toThrow("deadlock");
    await expect(openStatusWindow()).rejects.toThrow("deadlock");
  });
});
