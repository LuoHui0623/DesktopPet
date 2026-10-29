import { reportInteraction } from "../api/endpoints";
import type { InteractionConfig, InteractionKind } from "../api/types";
import { getApiClient } from "./api-client";

export const HOVER_DWELL_MS = 300;
export const CLIENT_REPORT_INTERVAL_MS = 1000;
export const DRAG_IDLE_MS = 350;
export const REACTION_MS = 220;

// 后端未返回配置时的兜底；真源仍是 app_settings，仅在加载完成前短暂生效。
export const FALLBACK_INTERACTION_CONFIG: InteractionConfig = {
  report_enabled_kinds: ["click", "hover", "drag", "menu"],
  hover_dwell_ms: HOVER_DWELL_MS,
  report_interval_ms: CLIENT_REPORT_INTERVAL_MS,
  reaction_ms: REACTION_MS,
};

type SetTimer = (callback: () => void, ms: number) => unknown;
type ClearTimer = (handle: unknown) => void;

const defaultSetTimer: SetTimer = (callback, ms) => globalThis.setTimeout(callback, ms);
const defaultClearTimer: ClearTimer = (handle) =>
  globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>);

export function createInteractionGate(
  config: InteractionConfig = FALLBACK_INTERACTION_CONFIG,
  now: () => number = Date.now,
): (kind: InteractionKind) => boolean {
  const enabled = new Set(config.report_enabled_kinds);
  const intervalMs = config.report_interval_ms;
  const last = new Map<InteractionKind, number>();
  return (kind) => {
    if (!enabled.has(kind)) return false;
    const stamp = now();
    const previous = last.get(kind);
    if (previous !== undefined && stamp - previous < intervalMs) return false;
    last.set(kind, stamp);
    return true;
  };
}

export async function reportPetInteraction(kind: InteractionKind): Promise<void> {
  try {
    const client = await getApiClient();
    await reportInteraction(client, kind);
  } catch (error: unknown) {
    console.error("上报互动失败", error);
  }
}

export interface ReactionController {
  show: (kind: InteractionKind) => void;
  stop: () => void;
}

export interface ReactionOptions {
  durationMs?: number;
  setTimer?: SetTimer;
  clearTimer?: ClearTimer;
}

export function createReactionController(
  onChange: (kind: InteractionKind | null) => void,
  options: ReactionOptions = {},
): ReactionController {
  const durationMs = options.durationMs ?? REACTION_MS;
  const setTimer = options.setTimer ?? defaultSetTimer;
  const clearTimer = options.clearTimer ?? defaultClearTimer;
  let handle: unknown = null;

  const stop = (): void => {
    if (handle === null) return;
    clearTimer(handle);
    handle = null;
  };

  return {
    show: (kind) => {
      stop();
      onChange(kind);
      handle = setTimer(() => {
        handle = null;
        onChange(null);
      }, durationMs);
    },
    stop,
  };
}

export interface HoverTrigger {
  start: () => void;
  stop: () => void;
}

export interface HoverOptions {
  dwellMs?: number;
  setTimer?: SetTimer;
  clearTimer?: ClearTimer;
}

export function createHoverTrigger(trigger: () => void, options: HoverOptions = {}): HoverTrigger {
  const dwellMs = options.dwellMs ?? HOVER_DWELL_MS;
  const setTimer = options.setTimer ?? defaultSetTimer;
  const clearTimer = options.clearTimer ?? defaultClearTimer;
  let handle: unknown = null;

  return {
    start: () => {
      if (handle !== null) return;
      handle = setTimer(() => {
        handle = null;
        trigger();
      }, dwellMs);
    },
    stop: () => {
      if (handle === null) return;
      clearTimer(handle);
      handle = null;
    },
  };
}

export interface DragTracker {
  start: () => void;
  move: () => void;
  end: () => void;
}

export interface DragOptions {
  idleMs?: number;
  setTimer?: SetTimer;
  clearTimer?: ClearTimer;
}

export function createDragTracker(onDrag: () => void, options: DragOptions = {}): DragTracker {
  const idleMs = options.idleMs ?? DRAG_IDLE_MS;
  const setTimer = options.setTimer ?? defaultSetTimer;
  const clearTimer = options.clearTimer ?? defaultClearTimer;
  let armed = false;
  let moved = false;
  let handle: unknown = null;

  const finish = (): void => {
    if (handle !== null) {
      clearTimer(handle);
      handle = null;
    }
    const shouldReport = armed && moved;
    armed = false;
    moved = false;
    if (shouldReport) onDrag();
  };

  return {
    start: () => {
      if (handle !== null) {
        clearTimer(handle);
        handle = null;
      }
      armed = true;
      moved = false;
    },
    move: () => {
      if (!armed) return;
      moved = true;
      if (handle !== null) clearTimer(handle);
      handle = setTimer(finish, idleMs);
    },
    end: finish,
  };
}
