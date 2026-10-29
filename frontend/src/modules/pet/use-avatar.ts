import { useEffect, useState } from "react";
import { loadAvatar, releaseAvatar, type LoadedAvatar } from "../../service/avatar.service";

export function useAvatar(revision = 0): LoadedAvatar | null {
  const [avatar, setAvatar] = useState<LoadedAvatar | null>(null);

  useEffect(() => {
    let cancelled = false;
    let loaded: LoadedAvatar | null = null;
    loadAvatar().then(
      (result) => {
        if (cancelled) {
          if (result !== null) releaseAvatar(result);
          return;
        }
        loaded = result;
        setAvatar(result);
      },
      (error: unknown) => {
        console.error("加载形象包失败", error);
      },
    );
    return () => {
      cancelled = true;
      if (loaded !== null) releaseAvatar(loaded);
    };
  }, [revision]);

  return avatar;
}
