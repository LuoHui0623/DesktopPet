import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { cursorPosition, getCurrentWindow } from "@tauri-apps/api/window";
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
import { isInteractiveAt, type InteractiveRect } from "./avatar-hit";
import type { LayoutRect } from "./avatar-layout";
import { createAvatarBehaviorResolver } from "./avatar-state";
import { createPassThroughController } from "./pass-through";
import { createPetHitState } from "./pet-hit-state";
import { PetView, type PetFrameProvider, type PetHitTest } from "./PetView";
import { ProactiveBubble } from "./ProactiveBubble";
import { petStatusLabel } from "./status";
import { useAvatar } from "./use-avatar";
import { useInteractionConfig } from "./use-interaction-config";

function openChat(history = false): void {
  openChatWindow({ history }).catch((error: unknown) => {
    console.error("打开对话窗口失败", error);
  });
}

function layoutOf(element: Element | null): LayoutRect | null {
  if (element === null) return null;
  const rect = element.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return null;
  return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
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
  const hitTestRef = useRef<PetHitTest | null>(null);
  const frameProviderRef = useRef<PetFrameProvider | null>(null);
  const mainRef = useRef<HTMLElement>(null);
  const labelRef = useRef<HTMLSpanElement>(null);
  const candidateRef = useRef<HTMLElement>(null);
  const proactiveRef = useRef<HTMLDivElement>(null);
  const uiRectsRef = useRef<InteractiveRect[]>([]);

  // 命中按形象的不透明像素判定：透明区域既不挥手也不开窗。
  const hits = useCallback(
    (clientX: number, clientY: number): boolean => hitTestRef.current?.(clientX, clientY) ?? true,
    [],
  );

  // 矩形在布局变化时刷新，轮询只读缓存，不为每次鼠标采样重新测量 DOM。
  const refreshUiRects = useCallback(() => {
    const rects: InteractiveRect[] = [];
    const labelRect = layoutOf(labelRef.current);
    if (labelRect !== null) rects.push({ rect: labelRect });
    const candidateRect = layoutOf(candidateRef.current);
    if (candidateRect !== null) rects.push({ rect: candidateRect, padding: 10 });
    const proactiveRect = layoutOf(proactiveRef.current);
    if (proactiveRect !== null) rects.push({ rect: proactiveRect, padding: 10 });
    uiRectsRef.current = rects;
  }, []);

  const uiRects = useCallback((): InteractiveRect[] => uiRectsRef.current, []);

  useLayoutEffect(() => {
    refreshUiRects();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(refreshUiRects);
    if (mainRef.current !== null) observer.observe(mainRef.current);
    if (labelRef.current !== null) observer.observe(labelRef.current);
    if (candidateRef.current !== null) observer.observe(candidateRef.current);
    if (proactiveRef.current !== null) observer.observe(proactiveRef.current);
    return () => observer.disconnect();
  }, [refreshUiRects, label, candidate, proactive]);

  useEffect(() => {
    window.addEventListener("resize", refreshUiRects);
    return () => window.removeEventListener("resize", refreshUiRects);
  }, [refreshUiRects]);

  const interactiveHit = useCallback(
    (clientX: number, clientY: number): boolean =>
      isInteractiveAt(frameProviderRef.current?.() ?? null, uiRects(), { x: clientX, y: clientY }),
    [uiRects],
  );
  // 状态机只保存回调，调用发生在事件与轮询里，不会在渲染期读取 ref。
  const hitState = useMemo(
    // eslint-disable-next-line react-hooks/refs
    () => createPetHitState((x, y) => interactiveHit(x, y)),
    [interactiveHit],
  );
  const hitStateRef = useRef(hitState);
  const hoverRef = useRef(hover);
  useEffect(() => {
    hitStateRef.current = hitState;
    hoverRef.current = hover;
  }, [hitState, hover]);

  const behavior = useMemo(
    () => createAvatarBehaviorResolver(undefined, avatar?.presetStates ?? []),
    [avatar?.presetStates],
  );
  const stateKey = behavior({ connected, status });

  const passThrough = useMemo(() => {
    const petWindow = getCurrentWindow();
    return createPassThroughController({
      window: {
        cursorPosition: () => cursorPosition(),
        innerPosition: () => petWindow.innerPosition(),
        innerSize: () => petWindow.innerSize(),
        setIgnoreCursorEvents: (ignore) => petWindow.setIgnoreCursorEvents(ignore),
      },
      viewportSize: () => ({ width: globalThis.innerWidth, height: globalThis.innerHeight }),
      resolve: (point) => isInteractiveAt(frameProviderRef.current?.() ?? null, uiRects(), point),
      onInteractiveChange: (interactive, point) => {
        if (!interactive) {
          hoverRef.current.stop();
          return;
        }
        if (hitStateRef.current.isLocked()) return;
        if (hits(point.x, point.y)) hoverRef.current.start();
        else hoverRef.current.stop();
      },
    });
  }, [hits, uiRects]);

  useEffect(() => {
    passThrough.start();
    return () => passThrough.stop();
  }, [passThrough]);

  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let stopped = false;
    listenSettingsChanged((domain) => {
      // 形象与互动参数都由后端下发；保存后立即重载，不等下一次轮询。
      // 人设切换会改变形象组，同样需要重新拉取目录。
      if (domain === "avatar" || domain === "persona") setAvatarVersion((value) => value + 1);
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
    const end = () => {
      drag.end();
      hitStateRef.current.up();
      passThrough.thaw();
    };
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
  }, [drag, passThrough, reactions]);

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
      ref={mainRef}
      role="button"
      tabIndex={0}
      aria-label={`宠物状态：${label}，点击打开对话`}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        if (!interactiveHit(event.clientX, event.clientY)) return;
        hitState.down(event.clientX, event.clientY);
        passThrough.freeze();
      }}
      onPointerMove={(event) => {
        if (hitState.isLocked()) return;
        if (hitState.move(event.clientX, event.clientY)) hover.start();
        else hover.stop();
      }}
      onPointerEnter={(event) => {
        if (hitState.isLocked()) return;
        if (hitState.enter(event.clientX, event.clientY)) hover.start();
      }}
      onPointerLeave={() => {
        if (hitState.isLocked()) return;
        hitState.leave();
        hover.stop();
      }}
      onClick={(event) => {
        if (!interactiveHit(event.clientX, event.clientY)) return;
        interact("click");
        openChat();
      }}
      onContextMenu={(event) => {
        event.preventDefault();
        drag.end();
        hitState.up();
        passThrough.thaw();
        interact("menu");
        menu.show().catch((error: unknown) => {
          console.error("弹出右键菜单失败", error);
        });
      }}
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
        width: "100vw",
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
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
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-start",
          gap: 8,
          padding: "8px 8px 0",
        }}
      >
        {candidate !== null && (
          <section
            ref={candidateRef}
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
          <div ref={proactiveRef}>
            <ProactiveBubble
              message={proactive}
              onOpen={() => {
                dismissProactive();
                openChat(true);
              }}
              onIgnore={ignoreProactive}
            />
          </div>
        )}
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
          paddingBottom: 6,
          flexShrink: 0,
        }}
      >
        <PetView
          state={stateKey}
          avatar={avatar}
          reaction={reaction}
          hitTestRef={hitTestRef}
          frameProviderRef={frameProviderRef}
        />
        <span
          ref={labelRef}
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
