import { invoke } from "@tauri-apps/api/core";
import {
  activateAvatar,
  getMenu,
  getSchedulerConfig,
  updateSchedulerConfig,
} from "../api/endpoints";
import type { MenuItem } from "../api/types";
import { getApiClient } from "./api-client";
import { MENU_ACTION_EVENT } from "./tauri-events";
import { openChatWindow, openSettingsWindow } from "./window.service";

export { MENU_ACTION_EVENT };
export const SHOW_CONTEXT_MENU_COMMAND = "show_context_menu";
export const EXIT_APP_COMMAND = "exit_app";

export const MENU_OPEN_CHAT = "open_chat";
export const MENU_OPEN_SETTINGS = "open_settings";
export const MENU_TOGGLE_PROACTIVE = "toggle_proactive";
export const MENU_QUIT = "quit";
export const AVATAR_MENU_PREFIX = "avatar:";

export interface MenuDeps {
  openChat: () => Promise<void>;
  openSettings: () => Promise<void>;
  exitApp: () => Promise<void>;
  toggleProactive: () => Promise<void>;
  activateAvatar: (packId: string) => Promise<void>;
  onAvatarChanged: () => void;
}

export function indexMenu(items: readonly MenuItem[]): Map<string, MenuItem> {
  const index = new Map<string, MenuItem>();
  const visit = (nodes: readonly MenuItem[]): void => {
    for (const node of nodes) {
      index.set(node.id, node);
      visit(node.children);
    }
  };
  visit(items);
  return index;
}

export async function dispatchMenuAction(item: MenuItem, deps: MenuDeps): Promise<boolean> {
  if (item.id === MENU_OPEN_CHAT) {
    await deps.openChat();
    return true;
  }
  if (item.id === MENU_OPEN_SETTINGS) {
    await deps.openSettings();
    return true;
  }
  if (item.id === MENU_TOGGLE_PROACTIVE) {
    await deps.toggleProactive();
    return true;
  }
  if (item.id === MENU_QUIT) {
    await deps.exitApp();
    return true;
  }
  if (item.id.startsWith(AVATAR_MENU_PREFIX)) {
    await deps.activateAvatar(item.id.slice(AVATAR_MENU_PREFIX.length));
    deps.onAvatarChanged();
    return true;
  }
  return false;
}

async function toggleProactiveMessages(): Promise<void> {
  const client = await getApiClient();
  const config = await getSchedulerConfig(client);
  await updateSchedulerConfig(client, { ...config, enabled: !config.enabled });
}

async function activateAvatarPack(packId: string): Promise<void> {
  const client = await getApiClient();
  await activateAvatar(client, packId);
}

export interface MenuController {
  show: () => Promise<void>;
  handle: (id: string) => Promise<void>;
}

export function createMenuController(overrides: Partial<MenuDeps> = {}): MenuController {
  const deps: MenuDeps = {
    openChat: async () => {
      await openChatWindow();
    },
    openSettings: async () => {
      await openSettingsWindow();
    },
    exitApp: async () => {
      await invoke(EXIT_APP_COMMAND);
    },
    toggleProactive: toggleProactiveMessages,
    activateAvatar: activateAvatarPack,
    onAvatarChanged: () => undefined,
    ...overrides,
  };
  let index = new Map<string, MenuItem>();
  return {
    show: async () => {
      const client = await getApiClient();
      const menu = await getMenu(client);
      index = indexMenu(menu.items);
      await invoke(SHOW_CONTEXT_MENU_COMMAND, { items: menu.items });
    },
    handle: async (id) => {
      const item = index.get(id);
      if (item !== undefined) await dispatchMenuAction(item, deps);
    },
  };
}
