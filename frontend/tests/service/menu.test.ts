import { describe, expect, it } from "vitest";
import type { MenuItem } from "../../src/api/types";
import {
  AVATAR_MENU_PREFIX,
  dispatchMenuAction,
  indexMenu,
  MENU_OPEN_CHAT,
  MENU_OPEN_SETTINGS,
  MENU_QUIT,
  MENU_TOGGLE_PROACTIVE,
  type MenuDeps,
} from "../../src/service/menu.service";

function item(id: string, overrides: Partial<MenuItem> = {}): MenuItem {
  return { id, label: id, enabled: true, checked: null, children: [], ...overrides };
}

function fakeDeps(): { calls: string[]; deps: MenuDeps } {
  const calls: string[] = [];
  return {
    calls,
    deps: {
      openChat: async () => {
        calls.push("chat");
      },
      openSettings: async () => {
        calls.push("settings");
      },
      exitApp: async () => {
        calls.push("exit");
      },
      toggleProactive: async () => {
        calls.push("proactive");
      },
      activateAvatar: async (packId) => {
        calls.push(`avatar:${packId}`);
      },
      onAvatarChanged: () => {
        calls.push("avatar-changed");
      },
    },
  };
}

describe("menu index", () => {
  it("includes nested children", () => {
    const index = indexMenu([
      item("switch_avatar", { children: [item("avatar:default"), item("avatar:cat")] }),
      item("quit"),
    ]);

    expect([...index.keys()]).toEqual(["switch_avatar", "avatar:default", "avatar:cat", "quit"]);
  });
});

describe("menu dispatch", () => {
  it("routes command items to their actions", async () => {
    const { calls, deps } = fakeDeps();

    expect(await dispatchMenuAction(item(MENU_OPEN_CHAT), deps)).toBe(true);
    expect(await dispatchMenuAction(item(MENU_OPEN_SETTINGS), deps)).toBe(true);
    expect(await dispatchMenuAction(item(MENU_TOGGLE_PROACTIVE, { checked: true }), deps)).toBe(
      true,
    );
    expect(await dispatchMenuAction(item(MENU_QUIT), deps)).toBe(true);

    expect(calls).toEqual(["chat", "settings", "proactive", "exit"]);
  });

  it("activates the avatar selected by id prefix", async () => {
    const { calls, deps } = fakeDeps();

    const handled = await dispatchMenuAction(item(`${AVATAR_MENU_PREFIX}cat`), deps);

    expect(handled).toBe(true);
    expect(calls).toEqual(["avatar:cat", "avatar-changed"]);
  });

  it("ignores unknown ids and submenu parents", async () => {
    const { calls, deps } = fakeDeps();

    expect(await dispatchMenuAction(item("switch_avatar"), deps)).toBe(false);
    expect(await dispatchMenuAction(item("unknown"), deps)).toBe(false);
    expect(calls).toEqual([]);
  });
});
