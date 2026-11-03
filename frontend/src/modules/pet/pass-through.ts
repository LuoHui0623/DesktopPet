import type { Point } from "./avatar-layout";

export interface PassThroughWindow {
  cursorPosition: () => Promise<Point>;
  innerPosition: () => Promise<Point>;
  scaleFactor: () => Promise<number>;
  setIgnoreCursorEvents: (ignore: boolean) => Promise<void>;
}

export const PASS_THROUGH_INTERVAL_MS = 16;

/** 全局光标物理坐标 → 窗口内 CSS 坐标。 */
export function physicalToWindowCss(cursor: Point, windowPosition: Point, scale: number): Point {
  if (!Number.isFinite(scale) || scale <= 0) return { x: 0, y: 0 };
  return {
    x: (cursor.x - windowPosition.x) / scale,
    y: (cursor.y - windowPosition.y) / scale,
  };
}

export interface PassThroughOptions {
  window: PassThroughWindow;
  resolve: (point: Point) => boolean;
  onInteractiveChange?: (interactive: boolean, point: Point) => void;
  intervalMs?: number;
  setTimer?: (callback: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export interface PassThroughController {
  start: () => void;
  stop: () => void;
  freeze: () => void;
  thaw: () => void;
  tick: () => Promise<void>;
  isInteractive: () => boolean;
  isFrozen: () => boolean;
}

const ZERO: Point = { x: 0, y: 0 };

/**
 * 轮询全局光标并切换窗口忽略开关。
 * 只在「可交互 / 穿透」结果变化时调用一次开关；按压或拖动期间冻结。
 */
export function createPassThroughController(options: PassThroughOptions): PassThroughController {
  const setTimer = options.setTimer ?? ((callback, ms) => globalThis.setInterval(callback, ms));
  const clearTimer =
    options.clearTimer ??
    ((handle) => globalThis.clearInterval(handle as ReturnType<typeof setInterval>));
  const intervalMs = options.intervalMs ?? PASS_THROUGH_INTERVAL_MS;

  let timer: unknown = null;
  let ticking = false;
  let frozen = false;
  let failed = false;
  let desiredInteractive = true;
  let appliedInteractive = true;
  let lastPoint: Point = ZERO;

  async function applyDesired(): Promise<void> {
    while (desiredInteractive !== appliedInteractive) {
      const targetInteractive = desiredInteractive;
      try {
        await options.window.setIgnoreCursorEvents(!targetInteractive);
        appliedInteractive = targetInteractive;
      } catch (error: unknown) {
        console.error("切换窗口穿透失败，退化为整窗可交互", error);
        failed = true;
        desiredInteractive = true;
        appliedInteractive = true;
        options.onInteractiveChange?.(true, lastPoint);
        try {
          await options.window.setIgnoreCursorEvents(false);
        } catch {
          // 忽略开关不可用时保持整窗可交互。
        }
        break;
      }
    }
  }

  function setInteractive(next: boolean, point: Point): void {
    if (next === desiredInteractive) return;
    desiredInteractive = next;
    options.onInteractiveChange?.(next, point);
    void applyDesired();
  }

  const controller: PassThroughController = {
    start: () => {
      if (timer !== null || failed) return;
      timer = setTimer(() => {
        void controller.tick();
      }, intervalMs);
      void controller.tick();
    },
    stop: () => {
      if (timer !== null) {
        clearTimer(timer);
        timer = null;
      }
      if (appliedInteractive === false) {
        desiredInteractive = true;
        void options.window.setIgnoreCursorEvents(false).catch(() => undefined);
      }
    },
    freeze: () => {
      frozen = true;
    },
    thaw: () => {
      if (!frozen) return;
      frozen = false;
      void controller.tick();
    },
    tick: async () => {
      if (failed || frozen || ticking) return;
      ticking = true;
      try {
        const [cursor, position, scale] = await Promise.all([
          options.window.cursorPosition(),
          options.window.innerPosition(),
          options.window.scaleFactor(),
        ]);
        const point = physicalToWindowCss(cursor, position, scale);
        lastPoint = point;
        let next = true;
        try {
          next = options.resolve(point);
        } catch (error: unknown) {
          console.error("计算可交互区域失败，按整窗可交互处理", error);
          next = true;
        }
        setInteractive(next, point);
      } catch (error: unknown) {
        console.error("读取全局光标失败，按整窗可交互处理", error);
        setInteractive(true, lastPoint);
      } finally {
        ticking = false;
      }
    },
    isInteractive: () => desiredInteractive,
    isFrozen: () => frozen,
  };

  return controller;
}
