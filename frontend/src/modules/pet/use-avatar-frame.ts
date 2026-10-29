import { useEffect, useState } from "react";
import type { AvatarAnimation } from "./avatar-view";

interface FrameTick {
  key: string;
  tick: number;
}

export function useAvatarFrame(animation: AvatarAnimation | null): string | undefined {
  const key = animation === null ? "" : `${animation.frameMs}:${animation.frames.join("\u0000")}`;
  const count = animation?.frames.length ?? 0;
  const frameMs = animation?.frameMs ?? 0;
  const [progress, setProgress] = useState<FrameTick>({ key, tick: 0 });

  useEffect(() => {
    if (count <= 1) return;
    const timer = setInterval(() => {
      setProgress((previous) =>
        previous.key === key ? { key, tick: previous.tick + 1 } : { key, tick: 1 },
      );
    }, frameMs);
    return () => clearInterval(timer);
  }, [key, count, frameMs]);

  if (animation === null || count === 0) return undefined;
  const tick = progress.key === key ? progress.tick : 0;
  return animation.frames[tick % count];
}
