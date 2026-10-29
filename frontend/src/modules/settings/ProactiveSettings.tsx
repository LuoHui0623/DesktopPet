import { useEffect, useState } from "react";
import type { SchedulerLedger, SettingsView } from "../../api/types";
import { loadSchedulerLedger, saveProactiveConfig } from "../../service/settings.service";
import {
  HINT_STYLE,
  NumberRow,
  SaveButton,
  Section,
  StatusLine,
  TextRow,
  ToggleRow,
} from "./settings-controls";
import { LIMITS, dndSummary, ledgerSummary, parseNumberField } from "./settings-view";
import { useSettingsSave } from "./use-settings-save";

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

interface Draft {
  enabled: boolean;
  intervalRaw: string;
  maxRaw: string;
  dndStart: string;
  dndEnd: string;
}

function toDraft(proactive: SettingsView["proactive"]): Draft {
  return {
    enabled: proactive.enabled,
    intervalRaw: String(proactive.min_interval_minutes),
    maxRaw: String(proactive.max_per_day),
    dndStart: proactive.dnd_start,
    dndEnd: proactive.dnd_end,
  };
}

function timeError(raw: string): string | null {
  return HHMM.test(raw.trim()) ? null : "应为 HH:MM";
}

export function ProactiveSettings({
  settings,
  onSaved,
}: {
  settings: SettingsView;
  onSaved: (view: SettingsView) => void;
}) {
  // 草稿初值来自后端下发的读模型；保存后返回的读模型与草稿一致，切换 tab 会重新挂载。
  const [draft, setDraft] = useState(() => toDraft(settings.proactive));
  const [ledger, setLedger] = useState<SchedulerLedger | null>(null);
  const { busy, error, notice, save } = useSettingsSave(onSaved);

  // 台账跟随调度配置刷新，保存后立刻显示当日已发送次数。
  useEffect(() => {
    let cancelled = false;
    loadSchedulerLedger().then(
      (next) => {
        if (!cancelled) setLedger(next);
      },
      () => {
        if (!cancelled) setLedger(null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [settings.proactive]);

  const interval = parseNumberField(
    draft.intervalRaw,
    settings.proactive.min_interval_minutes,
    LIMITS.minIntervalMinutes,
  );
  const max = parseNumberField(draft.maxRaw, settings.proactive.max_per_day, LIMITS.maxPerDay);
  const startError = timeError(draft.dndStart);
  const endError = timeError(draft.dndEnd);
  const invalid =
    interval.message !== null || max.message !== null || startError !== null || endError !== null;

  const onSave = () => {
    if (invalid) return;
    save(
      () =>
        saveProactiveConfig({
          enabled: draft.enabled,
          min_interval_minutes: interval.value,
          max_per_day: max.value,
          dnd_start: draft.dndStart.trim(),
          dnd_end: draft.dndEnd.trim(),
        }),
      "主动消息设置已保存，立即生效",
    );
  };

  return (
    <div>
      <Section title="开口许可" hint="与右键菜单“暂停主动消息”共用同一配置。">
        <ToggleRow
          label="允许主动消息"
          checked={draft.enabled}
          disabled={busy}
          onChange={(enabled) => setDraft({ ...draft, enabled })}
        />
      </Section>

      <Section title="频率限制">
        <NumberRow
          label="最小间隔（分钟）"
          value={draft.intervalRaw}
          disabled={busy}
          error={interval.message}
          onChange={(intervalRaw) => setDraft({ ...draft, intervalRaw })}
        />
        <NumberRow
          label="每日上限（次）"
          value={draft.maxRaw}
          disabled={busy}
          error={max.message}
          onChange={(maxRaw) => setDraft({ ...draft, maxRaw })}
        />
      </Section>

      <Section
        title={`勿扰时段（${dndSummary(settings)}）`}
        hint="起止相同表示不设勿扰；跨日自动识别。"
      >
        <TextRow
          label="开始"
          value={draft.dndStart}
          disabled={busy}
          error={startError}
          onChange={(dndStart) => setDraft({ ...draft, dndStart })}
        />
        <TextRow
          label="结束"
          value={draft.dndEnd}
          disabled={busy}
          error={endError}
          onChange={(dndEnd) => setDraft({ ...draft, dndEnd })}
        />
        <p style={HINT_STYLE}>{ledgerSummary(ledger)}</p>
      </Section>

      <Section title="保存">
        <SaveButton disabled={busy || invalid} onSave={onSave} />
        <StatusLine error={error} notice={notice} />
      </Section>
    </div>
  );
}
