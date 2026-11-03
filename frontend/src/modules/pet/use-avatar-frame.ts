import { useEffect, useState } from "react";
import {
  frameAt,
  timeToNextFrame,
  type AnimationFrame,
  type AnimationResource,
} from "./animation-resource";

function now(): number {
  return typeof performance === "undefined" ? Date.now() : performance.now();
}

/** 按资源累计时间轴播放；切换状态从新动画首帧开始。 */
export function useAvatarFrame(animation: AnimationResource | null): AnimationFrame | null {
  const [playback, setPlayback] = useState<{
    animation: AnimationResource;
    frame: AnimationFrame;
  } | null>(null);

  useEffect(() => {
    if (animation === null || animation.frames.length === 0) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const startedAt = now();
    let currentId: string | null = null;

    const tick = (): void => {
      if (cancelled) return;
      const elapsed = now() - startedAt;
      const next = frameAt(animation, elapsed);
      if (next !== null && next.id !== currentId) {
        currentId = next.id;
        setPlayback({ animation, frame: next });
      }
      timer = setTimeout(tick, Math.max(8, timeToNextFrame(animation, elapsed)));
    };

    tick();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [animation]);

  if (animation === null || animation.frames.length === 0) return null;
  if (playback !== null && playback.animation === animation) return playback.frame;
  return frameAt(animation, 0);
}
