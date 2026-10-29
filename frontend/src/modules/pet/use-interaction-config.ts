import { useEffect, useState } from "react";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { getInteractionConfig } from "../../api/endpoints";
import type { InteractionConfig } from "../../api/types";
import { getApiClient } from "../../service/api-client";
import { FALLBACK_INTERACTION_CONFIG } from "../../service/interaction.service";
import { listenSettingsChanged } from "../../service/settings.service";

export interface InteractionConfigDeps {
  load: () => Promise<InteractionConfig>;
  listen: (reload: () => void) => Promise<UnlistenFn>;
}

const defaultDeps: InteractionConfigDeps = {
  load: async () => getInteractionConfig(await getApiClient()),
  listen: async (reload) =>
    listenSettingsChanged((domain) => {
      if (domain === "interaction") reload();
    }),
};

// 悬浮窗只读消费互动配置：后端是唯一真源，加载失败时沿用兜底值，不阻塞形象渲染。
export function useInteractionConfig(deps: InteractionConfigDeps = defaultDeps): InteractionConfig {
  const { load, listen } = deps;
  const [config, setConfig] = useState<InteractionConfig>(FALLBACK_INTERACTION_CONFIG);

  useEffect(() => {
    let cancelled = false;
    let unlisten: UnlistenFn | null = null;
    const reload = () =>
      load().then(
        (next) => {
          if (!cancelled) setConfig(next);
        },
        (error: unknown) => {
          console.error("加载互动配置失败", error);
        },
      );
    void reload();
    listen(reload)
      .then((stop) => {
        if (cancelled) stop();
        else unlisten = stop;
      })
      .catch((error: unknown) => {
        console.error("监听设置变更失败", error);
      });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [load, listen]);

  return config;
}
