import { useState } from "react";
import type { PerceptionStateResponse, SettingsView } from "../../api/types";
import { describeRequestError } from "../../service/api-client";
import { clearPerception, savePerceptionConfig } from "../../service/settings.service";
import {
  BUTTON_STYLE,
  HINT_STYLE,
  NumberRow,
  SaveButton,
  Section,
  StatusLine,
  ToggleRow,
} from "./settings-controls";
import { LIMITS, parseNumberField } from "./settings-view";
import { useSettingsSave } from "./use-settings-save";

interface Draft {
  enabled: boolean;
  foregroundEnabled: boolean;
  awayRaw: string;
  sitRaw: string;
}

function toDraft(perception: SettingsView["perception"]): Draft {
  return {
    enabled: perception.enabled,
    foregroundEnabled: perception.foreground_enabled,
    awayRaw: String(perception.idle_away_minutes),
    sitRaw: String(perception.sit_minutes),
  };
}

export function PerceptionSettings({
  settings,
  perceptionState,
  onSaved,
}: {
  settings: SettingsView;
  perceptionState: PerceptionStateResponse | null;
  onSaved: (view: SettingsView) => void;
}) {
  const [draft, setDraft] = useState(() => toDraft(settings.perception));
  const [clearNotice, setClearNotice] = useState<string | null>(null);
  const [clearError, setClearError] = useState<string | null>(null);
  const { busy, error, notice, save } = useSettingsSave(onSaved);

  // 草稿初值来自后端下发的读模型；保存后返回的读模型与草稿一致，切换 tab 会重新挂载。
  const away = parseNumberField(
    draft.awayRaw,
    settings.perception.idle_away_minutes,
    LIMITS.idleAwayMinutes,
  );
  const sit = parseNumberField(draft.sitRaw, settings.perception.sit_minutes, LIMITS.sitMinutes);
  const invalid = away.message !== null || sit.message !== null;

  const onSave = () => {
    if (invalid) return;
    save(
      () =>
        savePerceptionConfig({
          enabled: draft.enabled,
          foreground_enabled: draft.foregroundEnabled,
          idle_away_minutes: away.value,
          sit_minutes: sit.value,
        }),
      "感知设置已保存，立即生效",
    );
  };

  const onClear = () => {
    setClearError(null);
    setClearNotice(null);
    clearPerception().then(
      () => setClearNotice("已清空内存中的感知记录"),
      (reason: unknown) => setClearError(describeRequestError(reason)),
    );
  };

  return (
    <div>
      <Section title="采集开关" hint="总开关关闭后 Rust 侧停止采样，前端也不再上报。">
        <ToggleRow
          label="启用感知采集"
          checked={draft.enabled}
          disabled={busy}
          onChange={(enabled) => setDraft({ ...draft, enabled })}
        />
        <ToggleRow
          label="采集前台进程"
          hint="仅关闭进程相关感知"
          checked={draft.foregroundEnabled}
          disabled={busy}
          onChange={(foregroundEnabled) => setDraft({ ...draft, foregroundEnabled })}
        />
      </Section>

      <Section title="判定阈值" hint="留空或超范围时不会保存，取值真源仍是后端。">
        <NumberRow
          label="离开判定（分钟）"
          value={draft.awayRaw}
          disabled={busy}
          error={away.message}
          onChange={(awayRaw) => setDraft({ ...draft, awayRaw })}
        />
        <NumberRow
          label="久坐判定（分钟）"
          value={draft.sitRaw}
          disabled={busy}
          error={sit.message}
          onChange={(sitRaw) => setDraft({ ...draft, sitRaw })}
        />
      </Section>

      <Section title="进程白名单" hint="只有白名单内的前台进程才会进入感知流水。">
        {perceptionState === null ? (
          <p style={HINT_STYLE}>白名单加载中…</p>
        ) : (
          <>
            <p style={HINT_STYLE}>
              白名单：
              {perceptionState.whitelist.length === 0
                ? "（空）"
                : perceptionState.whitelist.join("、")}
            </p>
            <p style={HINT_STYLE}>
              已忽略：
              {perceptionState.muted.length === 0 ? "（空）" : perceptionState.muted.join("、")}
            </p>
          </>
        )}
      </Section>

      <Section title="感知记录" hint="感知数据只存内存，清空不影响数据库。">
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <SaveButton disabled={busy || invalid} onSave={onSave} />
          <button type="button" style={BUTTON_STYLE} disabled={busy} onClick={onClear}>
            清空感知记录
          </button>
        </div>
        <StatusLine error={error ?? clearError} notice={notice ?? clearNotice} />
      </Section>
    </div>
  );
}
