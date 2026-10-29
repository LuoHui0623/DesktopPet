import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { decidePerceptionProcess, getPerceptionState, ingestPerception } from "../api/endpoints";
import type { PerceptionKind, PerceptionStateResponse, PerceptionValue } from "../api/types";
import { getApiClient } from "./api-client";
import { listenSettingsChanged } from "./settings.service";
import { PERCEPTION_CANDIDATE_EVENT, PERCEPTION_SAMPLE_EVENT } from "./tauri-events";
import { petStore } from "./stores/pet.store";

/** 事件名集中在 tauri-events.ts，与 Rust 侧常量一一对应。 */
export const SAMPLE_EVENT = PERCEPTION_SAMPLE_EVENT;
export const CANDIDATE_EVENT = PERCEPTION_CANDIDATE_EVENT;
export { PERCEPTION_SAMPLE_EVENT, PERCEPTION_CANDIDATE_EVENT };

const CONFIG_POLL_MS = 10_000;

interface RustSample {
  kind: PerceptionKind;
  value: PerceptionValue;
}

interface RustCandidate {
  exe: string;
}

interface CollectorConfig {
  foregroundEnabled: boolean;
  processWhitelist: string[];
  processMuted: string[];
}

function collectorConfig(state: PerceptionStateResponse): CollectorConfig {
  return {
    foregroundEnabled: state.foreground_enabled,
    processWhitelist: state.whitelist,
    processMuted: state.muted,
  };
}

async function updateCollector(state: PerceptionStateResponse): Promise<void> {
  await invoke("update_perception_config", { config: collectorConfig(state) });
}

export async function resolvePerceptionCandidate(exe: string, allow: boolean): Promise<void> {
  const client = await getApiClient();
  const state = await decidePerceptionProcess(client, {
    exe,
    decision: allow ? "allow" : "ignore",
  });
  await updateCollector(state);
  petStore.getState().setPerceptionCandidate(null);
}

export async function startPerception(): Promise<() => void> {
  const client = await getApiClient();
  let unlisteners: UnlistenFn[] = [];
  let running = false;
  let stopped = false;
  let refreshing = false;
  let timer: ReturnType<typeof setInterval> | null = null;

  const forward = async (sample: RustSample): Promise<void> => {
    if (stopped) return;
    try {
      await ingestPerception(client, {
        kind: sample.kind,
        value: sample.value,
        at: new Date().toISOString(),
      });
    } catch (error: unknown) {
      console.error("上报感知样本失败", error);
    }
  };

  const candidate = async (payload: RustCandidate): Promise<void> => {
    if (stopped) return;
    try {
      const result = await ingestPerception(client, {
        kind: "process_candidate",
        value: payload.exe,
        at: new Date().toISOString(),
      });
      if (result.accepted && !stopped) {
        petStore.getState().setPerceptionCandidate(payload.exe);
      }
    } catch (error: unknown) {
      console.error("上报候选进程失败", error);
    }
  };

  const attach = async (): Promise<void> => {
    if (unlisteners.length > 0) return;
    unlisteners = await Promise.all([
      listen<RustSample>(SAMPLE_EVENT, (event) => {
        void forward(event.payload);
      }),
      listen<RustCandidate>(CANDIDATE_EVENT, (event) => {
        void candidate(event.payload);
      }),
    ]);
  };

  const detach = async (): Promise<void> => {
    const current = unlisteners;
    unlisteners = [];
    await Promise.all(current.map((unlisten) => unlisten()));
  };

  const stopCollector = async (): Promise<void> => {
    await detach();
    if (running) {
      running = false;
      await invoke("stop_perception");
    }
  };

  const apply = async (state: PerceptionStateResponse): Promise<void> => {
    if (stopped) return;
    if (!state.enabled) {
      await stopCollector();
      petStore.getState().setPerceptionCandidate(null);
      return;
    }
    await attach();
    if (running) {
      await updateCollector(state);
    } else {
      await invoke("start_perception", { config: collectorConfig(state) });
      running = true;
    }
  };

  const refresh = async (): Promise<void> => {
    if (refreshing || stopped) return;
    refreshing = true;
    try {
      await apply(await getPerceptionState(client));
    } catch (error: unknown) {
      console.error("同步感知开关失败", error);
    } finally {
      refreshing = false;
    }
  };

  await refresh();
  timer = setInterval(() => {
    void refresh();
  }, CONFIG_POLL_MS);

  let unlistenSettings: UnlistenFn | null = null;
  let settingsStopped = false;
  listenSettingsChanged((domain) => {
    if (domain === "perception") void refresh();
  })
    .then((stop) => {
      if (settingsStopped || stopped) stop();
      else unlistenSettings = stop;
    })
    .catch((error: unknown) => {
      console.error("监听感知设置变更失败", error);
    });

  return () => {
    stopped = true;
    settingsStopped = true;
    if (timer !== null) clearInterval(timer);
    unlistenSettings?.();
    petStore.getState().setPerceptionCandidate(null);
    void stopCollector().catch((error: unknown) => {
      console.error("停止感知采集失败", error);
    });
  };
}
