import { afterEach, describe, expect, it, vi } from "vitest";
import {
  PASS_THROUGH_INTERVAL_MS,
  createPassThroughController,
  physicalToWindowCss,
  type PassThroughWindow,
} from "../../src/modules/pet/pass-through";

function makeWindow(overrides: Partial<PassThroughWindow> = {}): PassThroughWindow {
  return {
    cursorPosition: vi.fn(async () => ({ x: 200, y: 300 })),
    innerPosition: vi.fn(async () => ({ x: 100, y: 200 })),
    scaleFactor: vi.fn(async () => 2),
    setIgnoreCursorEvents: vi.fn(async () => undefined),
    ...overrides,
  };
}

function makeTimers() {
  let callback: (() => void) | null = null;
  const setTimer = vi.fn((next: () => void) => {
    callback = next;
    return 1;
  });
  const clearTimer = vi.fn();
  return {
    setTimer,
    clearTimer,
    fire: () => callback?.(),
  };
}

async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("physicalToWindowCss", () => {
  it("converts physical cursor coordinates into window CSS coordinates", () => {
    expect(physicalToWindowCss({ x: 200, y: 300 }, { x: 100, y: 200 }, 2)).toEqual({
      x: 50,
      y: 50,
    });
  });

  it("supports fractional scaling", () => {
    expect(physicalToWindowCss({ x: 175, y: 225 }, { x: 100, y: 200 }, 1.5)).toEqual({
      x: 50,
      y: 16.666666666666668,
    });
    expect(physicalToWindowCss({ x: 250, y: 250 }, { x: 100, y: 100 }, 1.25)).toEqual({
      x: 120,
      y: 120,
    });
    expect(physicalToWindowCss({ x: 200, y: 300 }, { x: 100, y: 200 }, 1)).toEqual({
      x: 100,
      y: 100,
    });
  });

  it("returns the origin for an invalid scale", () => {
    expect(physicalToWindowCss({ x: 10, y: 10 }, { x: 5, y: 5 }, 0)).toEqual({ x: 0, y: 0 });
    expect(physicalToWindowCss({ x: 10, y: 10 }, { x: 5, y: 5 }, Number.NaN)).toEqual({
      x: 0,
      y: 0,
    });
  });
});

describe("createPassThroughController", () => {
  it("does nothing while the pointer stays interactive", async () => {
    const window = makeWindow();
    const controller = createPassThroughController({ window, resolve: () => true });

    await controller.tick();

    expect(window.cursorPosition).toHaveBeenCalledTimes(1);
    expect(controller.isInteractive()).toBe(true);
    expect(window.setIgnoreCursorEvents).not.toHaveBeenCalled();
  });

  it("switches to pass-through only when the result changes", async () => {
    const window = makeWindow();
    const changes: boolean[] = [];
    const controller = createPassThroughController({
      window,
      resolve: () => false,
      onInteractiveChange: (interactive) => changes.push(interactive),
    });

    await controller.tick();
    await flush();
    await controller.tick();
    await flush();

    expect(controller.isInteractive()).toBe(false);
    expect(changes).toEqual([false]);
    expect(window.setIgnoreCursorEvents).toHaveBeenCalledTimes(1);
    expect(window.setIgnoreCursorEvents).toHaveBeenCalledWith(true);
  });

  it("switches back when the pointer enters an interactive pixel", async () => {
    const window = makeWindow();
    let interactive = false;
    const controller = createPassThroughController({
      window,
      resolve: () => interactive,
    });

    await controller.tick();
    await flush();
    interactive = true;
    await controller.tick();
    await flush();

    expect(window.setIgnoreCursorEvents).toHaveBeenLastCalledWith(false);
  });

  it("passes window-relative CSS coordinates to the resolver", async () => {
    const window = makeWindow();
    const resolve = vi.fn(() => true);
    const controller = createPassThroughController({ window, resolve });

    await controller.tick();

    expect(resolve).toHaveBeenCalledWith({ x: 50, y: 50 });
  });

  it("freezes lookups during a press and thaws afterwards", async () => {
    const window = makeWindow();
    const controller = createPassThroughController({ window, resolve: () => false });

    controller.freeze();
    await controller.tick();
    expect(window.cursorPosition).not.toHaveBeenCalled();
    expect(controller.isFrozen()).toBe(true);

    controller.thaw();
    await flush();
    expect(window.cursorPosition).toHaveBeenCalledTimes(1);
    expect(controller.isFrozen()).toBe(false);
  });

  it("falls back to interactive when cursor reading fails", async () => {
    const window = makeWindow({
      cursorPosition: vi.fn(async () => {
        throw new Error("no cursor");
      }),
    });
    vi.spyOn(console, "error").mockImplementation(() => {});
    const controller = createPassThroughController({ window, resolve: () => false });

    await controller.tick();

    expect(controller.isInteractive()).toBe(true);
  });

  it("falls back to interactive when the resolver throws", async () => {
    const window = makeWindow();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const controller = createPassThroughController({
      window,
      resolve: () => {
        throw new Error("bad frame");
      },
    });

    await controller.tick();

    expect(controller.isInteractive()).toBe(true);
  });

  it("disables switching permanently after a failed write", async () => {
    const window = makeWindow({
      setIgnoreCursorEvents: vi.fn(async () => {
        throw new Error("unsupported");
      }),
    });
    vi.spyOn(console, "error").mockImplementation(() => {});
    const controller = createPassThroughController({ window, resolve: () => false });

    await controller.tick();
    await flush();

    expect(controller.isInteractive()).toBe(true);
    expect(window.setIgnoreCursorEvents).toHaveBeenCalledWith(false);

    vi.mocked(window.cursorPosition).mockClear();
    controller.start();
    await flush();
    expect(window.cursorPosition).not.toHaveBeenCalled();
  });

  it("starts polling with the configured interval", async () => {
    const window = makeWindow();
    const timers = makeTimers();
    const controller = createPassThroughController({
      window,
      resolve: () => true,
      intervalMs: 40,
      setTimer: timers.setTimer,
      clearTimer: timers.clearTimer,
    });

    controller.start();
    await flush();

    expect(timers.setTimer).toHaveBeenCalledWith(expect.any(Function), 40);
    expect(window.cursorPosition).toHaveBeenCalledTimes(1);

    timers.fire();
    await flush();
    expect(window.cursorPosition).toHaveBeenCalledTimes(2);

    controller.stop();
    expect(timers.clearTimer).toHaveBeenCalledWith(1);
  });

  it("uses the 16ms polling interval by default", () => {
    expect(PASS_THROUGH_INTERVAL_MS).toBe(16);
  });

  it("restores window interactivity when stopped while ignoring events", async () => {
    const window = makeWindow();
    const controller = createPassThroughController({ window, resolve: () => false });

    await controller.tick();
    await flush();
    controller.stop();
    await flush();

    expect(window.setIgnoreCursorEvents).toHaveBeenLastCalledWith(false);
  });

  it("serializes overlapping ticks", async () => {
    const window = makeWindow();
    const controller = createPassThroughController({ window, resolve: () => true });

    await Promise.all([controller.tick(), controller.tick()]);

    expect(window.cursorPosition).toHaveBeenCalledTimes(1);
  });
});
