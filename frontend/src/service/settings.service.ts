import { emitTo, listen, type UnlistenFn } from "@tauri-apps/api/event";
import {
  clearPerceptionRecords,
  getSchedulerLedger,
  getSettings,
  updateSettingsAvatar,
  updateSettingsInteraction,
  updateSettingsPerception,
  updateSettingsProactive,
} from "../api/endpoints";
import type {
  InteractionConfig,
  PerceptionConfig,
  SchedulerConfig,
  SchedulerLedger,
  SettingsView,
} from "../api/types";
import { getApiClient } from "./api-client";
import { SETTINGS_CHANGED_EVENT } from "./tauri-events";

export type SettingsDomain = "perception" | "proactive" | "avatar" | "interaction";

/** 悬浮窗标签与 src-tauri/src/window.rs 的 PET_WINDOW_LABEL 保持一致。 */
export const PET_WINDOW_LABEL = "pet";
export { SETTINGS_CHANGED_EVENT };

export interface SettingsChangedPayload {
  domain: SettingsDomain;
}

// 设置页保存后通知悬浮窗立即同步；不在 Tauri 环境（单测/浏览器预览）时静默跳过。
export async function notifyPetSettingsChanged(domain: SettingsDomain): Promise<void> {
  try {
    await emitTo(PET_WINDOW_LABEL, SETTINGS_CHANGED_EVENT, {
      domain,
    } satisfies SettingsChangedPayload);
  } catch (error: unknown) {
    console.error("通知悬浮窗同步设置失败", error);
  }
}

export async function listenSettingsChanged(
  handler: (domain: SettingsDomain) => void,
): Promise<UnlistenFn> {
  return listen<SettingsChangedPayload>(SETTINGS_CHANGED_EVENT, (event) => {
    handler(event.payload.domain);
  });
}

export function loadSettings(): Promise<SettingsView> {
  return getApiClient().then(getSettings);
}

export function loadSchedulerLedger(): Promise<SchedulerLedger> {
  return getApiClient().then(getSchedulerLedger);
}

export function savePerceptionConfig(config: PerceptionConfig): Promise<SettingsView> {
  return getApiClient()
    .then((client) => updateSettingsPerception(client, config))
    .then(async (view) => {
      await notifyPetSettingsChanged("perception");
      return view;
    });
}

export function saveProactiveConfig(config: SchedulerConfig): Promise<SettingsView> {
  return getApiClient()
    .then((client) => updateSettingsProactive(client, config))
    .then(async (view) => {
      await notifyPetSettingsChanged("proactive");
      return view;
    });
}

export function saveActiveAvatar(id: string): Promise<SettingsView> {
  return getApiClient()
    .then((client) => updateSettingsAvatar(client, id))
    .then(async (view) => {
      await notifyPetSettingsChanged("avatar");
      return view;
    });
}

export function saveInteractionConfig(config: InteractionConfig): Promise<SettingsView> {
  return getApiClient()
    .then((client) => updateSettingsInteraction(client, config))
    .then(async (view) => {
      await notifyPetSettingsChanged("interaction");
      return view;
    });
}

export function clearPerception(): Promise<{ cleared: boolean }> {
  return getApiClient().then(clearPerceptionRecords);
}
