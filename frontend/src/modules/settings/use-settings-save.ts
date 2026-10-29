import { useCallback, useState } from "react";
import type { SettingsView } from "../../api/types";
import { describeRequestError } from "../../service/api-client";

export interface SettingsSaveState {
  busy: boolean;
  error: string | null;
  notice: string | null;
  save: (task: () => Promise<SettingsView>, message: string) => void;
}

export function useSettingsSave(onSaved: (view: SettingsView) => void): SettingsSaveState {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const save = useCallback(
    (task: () => Promise<SettingsView>, message: string) => {
      setBusy(true);
      setError(null);
      setNotice(null);
      task()
        .then(
          (view) => {
            onSaved(view);
            setNotice(message);
          },
          (reason: unknown) => {
            setError(describeRequestError(reason));
          },
        )
        .finally(() => {
          setBusy(false);
        });
    },
    [onSaved],
  );

  return { busy, error, notice, save };
}
