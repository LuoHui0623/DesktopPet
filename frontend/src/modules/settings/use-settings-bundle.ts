import { useCallback, useEffect, useRef, useState } from "react";
import type { PerceptionStateResponse, SettingsView } from "../../api/types";
import { getPerceptionState } from "../../api/endpoints";
import { getApiClient } from "../../service/api-client";
import { loadSettings } from "../../service/settings.service";

export interface SettingsBundle {
  view: SettingsView | null;
  perceptionState: PerceptionStateResponse | null;
  error: string | null;
  apply: (view: SettingsView) => void;
  reload: () => void;
}

export function useSettingsBundle(): SettingsBundle {
  const [view, setView] = useState<SettingsView | null>(null);
  const [perceptionState, setState] = useState<PerceptionStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 每次读取占一个世代号，卸载或重新读取后旧响应直接作废
  const generation = useRef(0);

  const load = useCallback(() => {
    generation.current += 1;
    const token = generation.current;
    Promise.all([loadSettings(), getApiClient().then(getPerceptionState)]).then(
      ([nextView, nextState]) => {
        if (generation.current !== token) return;
        setView(nextView);
        setState(nextState);
        setError(null);
      },
      () => {
        if (generation.current === token) setError("无法读取设置，请确认后端已启动");
      },
    );
  }, []);

  useEffect(() => {
    load();
    return () => {
      generation.current += 1;
    };
  }, [load]);

  const apply = useCallback((next: SettingsView) => {
    setView(next);
    getApiClient()
      .then(getPerceptionState)
      .then(
        (state) => setState(state),
        () => undefined,
      );
  }, []);

  return { view, perceptionState, error, apply, reload: load };
}
