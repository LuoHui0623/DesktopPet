import { useState } from "react";
import type { InteractionKind, SettingsView } from "../../api/types";
import { saveInteractionConfig } from "../../service/settings.service";
import { SaveButton, Section, StatusLine, NumberRow, ToggleRow } from "./settings-controls";
import { INTERACTION_KINDS, INTERACTION_LABELS, LIMITS, parseNumberField } from "./settings-view";
import { useSettingsSave } from "./use-settings-save";

interface Draft {
  enabled: Record<InteractionKind, boolean>;
  dwellRaw: string;
  intervalRaw: string;
  reactionRaw: string;
}

function toDraft(interaction: SettingsView["interaction"]): Draft {
  const enabled = {} as Record<InteractionKind, boolean>;
  for (const kind of INTERACTION_KINDS) {
    enabled[kind] = interaction.report_enabled_kinds.includes(kind);
  }
  return {
    enabled,
    dwellRaw: String(interaction.hover_dwell_ms),
    intervalRaw: String(interaction.report_interval_ms),
    reactionRaw: String(interaction.reaction_ms),
  };
}

export function InteractionSettings({
  settings,
  onSaved,
}: {
  settings: SettingsView;
  onSaved: (view: SettingsView) => void;
}) {
  const [draft, setDraft] = useState(() => toDraft(settings.interaction));
  const { busy, error, notice, save } = useSettingsSave(onSaved);

  // 草稿初值来自后端下发的读模型；保存后返回的读模型与草稿一致，切换 tab 会重新挂载。
  const dwell = parseNumberField(
    draft.dwellRaw,
    settings.interaction.hover_dwell_ms,
    LIMITS.hoverDwellMs,
  );
  const interval = parseNumberField(
    draft.intervalRaw,
    settings.interaction.report_interval_ms,
    LIMITS.reportIntervalMs,
  );
  const reaction = parseNumberField(
    draft.reactionRaw,
    settings.interaction.reaction_ms,
    LIMITS.reactionMs,
  );
  const invalid = dwell.message !== null || interval.message !== null || reaction.message !== null;

  const onSave = () => {
    if (invalid) return;
    save(
      () =>
        saveInteractionConfig({
          report_enabled_kinds: INTERACTION_KINDS.filter((kind) => draft.enabled[kind]),
          hover_dwell_ms: dwell.value,
          report_interval_ms: interval.value,
          reaction_ms: reaction.value,
        }),
      "互动设置已保存，立即生效",
    );
  };

  return (
    <div>
      <Section title="上报行为" hint="关闭后该行为只保留前端反应，不进入对话上下文。">
        {INTERACTION_KINDS.map((kind) => (
          <ToggleRow
            key={kind}
            label={INTERACTION_LABELS[kind]}
            checked={draft.enabled[kind]}
            disabled={busy}
            onChange={(next) => setDraft({ ...draft, enabled: { ...draft.enabled, [kind]: next } })}
          />
        ))}
      </Section>

      <Section title="节流与反应" hint="取值真源在后端，留空或超范围不会保存。">
        <NumberRow
          label="悬停停留（毫秒）"
          value={draft.dwellRaw}
          disabled={busy}
          error={dwell.message}
          onChange={(dwellRaw) => setDraft({ ...draft, dwellRaw })}
        />
        <NumberRow
          label="同类上报最小间隔（毫秒）"
          value={draft.intervalRaw}
          disabled={busy}
          error={interval.message}
          onChange={(intervalRaw) => setDraft({ ...draft, intervalRaw })}
        />
        <NumberRow
          label="前端反应时长（毫秒）"
          value={draft.reactionRaw}
          disabled={busy}
          error={reaction.message}
          onChange={(reactionRaw) => setDraft({ ...draft, reactionRaw })}
        />
      </Section>

      <Section title="保存">
        <SaveButton disabled={busy || invalid} onSave={onSave} />
        <StatusLine error={error} notice={notice} />
      </Section>
    </div>
  );
}
