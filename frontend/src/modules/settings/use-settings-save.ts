import { useCallback, useState } from "react";
import type { SettingsView } from "../../api/types";
import { describeRequestError } from "../../service/api-client";

export interface SettingsSaveState {
  busy: boolean;
  error: string | null;
  notice: string | null;
  save: (task: (version: number) => Promise<SettingsView>, message: string) => void;
}

/** `version` 来自当前设置快照，作为 `If-Match` 传给后端；保存成功后由新快照覆盖。 */
export function useSettingsSave(
  version: number,
  onSaved: (view: SettingsView) => void,
): SettingsSaveState {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const save = useCallback(
    (task: (version: number) => Promise<SettingsView>, message: string) => {
      setBusy(true);
      setError(null);
      setNotice(null);
      task(version)
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
    [onSaved, version],
  );

  return { busy, error, notice, save };
}
