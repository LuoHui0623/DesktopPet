import { invoke } from "@tauri-apps/api/core";

export const OPEN_CHAT_WINDOW_COMMAND = "open_chat_window";
export const OPEN_SETTINGS_WINDOW_COMMAND = "open_settings_window";

export function openChatWindow(): Promise<void> {
  return invoke<void>(OPEN_CHAT_WINDOW_COMMAND);
}

export function openSettingsWindow(): Promise<void> {
  return invoke<void>(OPEN_SETTINGS_WINDOW_COMMAND);
}
