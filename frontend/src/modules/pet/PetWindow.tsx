import { useCallback, useEffect, useMemo, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { useStore } from "zustand";
import type { InteractionKind } from "../../api/types";
import {
  createDragTracker,
  createHoverTrigger,
  createInteractionGate,
  createReactionController,
  reportPetInteraction,
} from "../../service/interaction.service";
import { createMenuController, MENU_ACTION_EVENT } from "../../service/menu.service";
import { resolvePerceptionCandidate } from "../../service/perception.service";
import { listenSettingsChanged } from "../../service/settings.service";
import { openChatWindow } from "../../service/window.service";
import { petStore } from "../../service/stores/pet.store";
import { PetView } from "./PetView";
import { ProactiveBubble } from "./ProactiveBubble";
import { petStatusLabel } from "./status";
import { useAvatar } from "./use-avatar";
import { useInteractionConfig } from "./use-interaction-config";

function openChat(history = false): void {
  openChatWindow({ history }).catch((error: unknown) => {
    console.error("打开对话窗口失败", error);
  });
}

export function PetWindow() {
  const connected = useStore(petStore, (state) => state.connected);
  const status = useStore(petStore, (state) => state.status);
  const candidate = useStore(petStore, (state) => state.perceptionCandidate);
  const proactive = useStore(petStore, (state) => state.proactiveMessages[0] ?? null);
  const dismissProactive = useStore(petStore, (state) => state.dismissProactive);
  const ignoreProactive = useStore(petStore, (state) => state.ignoreProactive);
  const [avatarVersion, setAvatarVersion] = useState(0);
  const avatar = useAvatar(avatarVersion);
  const label = petStatusLabel(connected, status);
  const interactionConfig = useInteractionConfig();
  const [reaction, setReaction] = useState<InteractionKind | null>(null);
  const reactions = useMemo(
    () => createReactionController(setReaction, { durationMs: interactionConfig.reaction_ms }),
    [interactionConfig.reaction_ms],
  );
  const shouldReport = useMemo(() => createInteractionGate(interactionConfig), [interactionConfig]);
  const [menu] = useState(() =>
    createMenuController({
      onAvatarChanged: () => setAvatarVersion((value) => value + 1),
    }),
  );
  const interact = useCallback(
    (kind: InteractionKind) => {
      if (!shouldReport(kind)) return;
      reactions.show(kind);
      void reportPetInteraction(kind);
    },
    [shouldReport, reactions],
  );
  const hover = useMemo(
    () =>
      createHoverTrigger(() => interact("hover"), {
        dwellMs: interactionConfig.hover_dwell_ms,
      }),
    [interact, interactionConfig.hover_dwell_ms],
  );
  const drag = useMemo(() => createDragTracker(() => interact("drag")), [interact]);

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let stopped = false;
    listenSettingsChanged((domain) => {
      // 形象与互动参数都由后端下发；保存后立即重载，不等下一次轮询。
      if (domain === "avatar") setAvatarVersion((value) => value + 1);
    })
      .then((stop) => {
        if (stopped) stop();
        else unlisten = stop;
      })
      .catch((error: unknown) => {
        console.error("监听设置变更失败", error);
      });
    return () => {
      stopped = true;
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    const arm = () => drag.start();
    const end = () => drag.end();
    window.addEventListener("pointerdown", arm);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);

    let unlisten: (() => void) | null = null;
    let stopped = false;
    getCurrentWindow()
      .onMoved(() => drag.move())
      .then((stop) => {
        if (stopped) stop();
        else unlisten = stop;
      })
      .catch((error: unknown) => {
        console.error("监听窗口移动失败", error);
      });

    return () => {
      stopped = true;
      unlisten?.();
      window.removeEventListener("pointerdown", arm);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      reactions.stop();
    };
  }, [drag, reactions]);

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let stopped = false;
    listen<string>(MENU_ACTION_EVENT, (event) => {
      menu.handle(event.payload).catch((error: unknown) => {
        console.error("处理右键菜单失败", error);
      });
    })
      .then((stop) => {
        if (stopped) stop();
        else unlisten = stop;
      })
      .catch((error: unknown) => {
        console.error("监听右键菜单失败", error);
      });

    return () => {
      stopped = true;
      unlisten?.();
    };
  }, [menu]);

  return (
    <main
      data-tauri-drag-region
      role="button"
      tabIndex={0}
      aria-label={`宠物状态：${label}，点击打开对话`}
      onClick={() => {
        interact("click");
        openChat();
      }}
      onContextMenu={(event) => {
        event.preventDefault();
        drag.end();
        interact("menu");
        menu.show().catch((error: unknown) => {
          console.error("弹出右键菜单失败", error);
        });
      }}
      onPointerEnter={hover.start}
      onPointerLeave={hover.stop}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          interact("click");
          openChat();
        }
      }}
      style={{
        position: "relative",
        width: 320,
        height: 360,
        display: "flex",
        flexDirection: "column",
        background: "transparent",
        color: "#222",
        fontFamily: "system-ui, sans-serif",
        userSelect: "none",
        cursor: "pointer",
      }}
    >
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-start",
          gap: 8,
          padding: "8px 8px 0",
        }}
      >
        {candidate !== null && (
          <section
            aria-label="感知授权"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
            style={{
              padding: "8px 10px",
              borderRadius: 12,
              background: "rgba(255,255,255,0.96)",
              boxShadow: "0 6px 18px rgba(0,0,0,0.16)",
              fontSize: 13,
              lineHeight: 1.4,
            }}
          >
            <p style={{ margin: "0 0 6px" }}>允许「{candidate}」成为可感知的应用？</p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button
                type="button"
                onClick={() => {
                  resolvePerceptionCandidate(candidate, true).catch((error: unknown) => {
                    console.error("允许应用感知失败", error);
                  });
                }}
              >
                允许
              </button>
              <button
                type="button"
                onClick={() => {
                  resolvePerceptionCandidate(candidate, false).catch((error: unknown) => {
                    console.error("忽略应用感知失败", error);
                  });
                }}
              >
                忽略
              </button>
            </div>
          </section>
        )}
        {proactive !== null && (
          <ProactiveBubble
            message={proactive}
            onOpen={() => {
              dismissProactive();
              openChat(true);
            }}
            onIgnore={ignoreProactive}
          />
        )}
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 6,
          paddingBottom: 6,
        }}
      >
        <PetView state={connected ? status : null} avatar={avatar} reaction={reaction} />
        <span
          data-tauri-drag-region
          style={{
            padding: "2px 10px",
            borderRadius: 10,
            background: connected ? "rgba(255,255,255,0.85)" : "rgba(120,120,120,0.85)",
            color: connected ? "#222" : "#fff",
            fontSize: 13,
          }}
        >
          {label}
        </span>
      </div>
    </main>
  );
}
