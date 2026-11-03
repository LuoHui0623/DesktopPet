import { useEffect, useState } from "react";
import type { AvatarPack, SettingsView } from "../../api/types";
import { loadAvatarFrameUrl } from "../../service/avatar.service";
import { saveActiveAvatar } from "../../service/settings.service";
import { SaveButton, Section, StatusLine, HINT_STYLE } from "./settings-controls";
import { packStatesText, previewFrame } from "./settings-view";
import { useSettingsSave } from "./use-settings-save";

function PackPreview({ persona, pack }: { persona: string; pack: AvatarPack }) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    const frame = previewFrame(pack);
    if (frame === null) return;
    let objectUrl: string | null = null;
    let cancelled = false;
    loadAvatarFrameUrl(persona, pack.id, frame).then(
      (next) => {
        if (cancelled) {
          URL.revokeObjectURL(next);
          return;
        }
        objectUrl = next;
        setUrl(next);
      },
      () => setUrl(null),
    );
    return () => {
      cancelled = true;
      if (objectUrl !== null) URL.revokeObjectURL(objectUrl);
    };
  }, [persona, pack]);

  const { width, height } = pack.frame;
  return url === null ? (
    <span
      aria-label={`${pack.name} 预览不可用`}
      style={{
        width: 48,
        height: 48,
        display: "inline-block",
        background: "#f3f4f6",
        borderRadius: 8,
      }}
    />
  ) : (
    <img
      src={url}
      alt={`${pack.name} 预览`}
      width={48}
      height={48}
      style={{ objectFit: "contain" }}
      data-frame-size={`${width}x${height}`}
    />
  );
}

export function AvatarSettings({
  settings,
  onSaved,
}: {
  settings: SettingsView;
  onSaved: (view: SettingsView) => void;
}) {
  const [selected, setSelected] = useState<string | null>(() => settings.avatar.active);
  const { busy, error, notice, save } = useSettingsSave(onSaved);
  const persona = settings.avatar.persona;

  // 选中项初值来自后端下发的读模型；保存后 active 与选中一致，切换 tab 会重新挂载。
  const dirty = selected !== null && selected !== settings.avatar.active;
  const onSave = () => {
    if (selected === null || !dirty) return;
    save(() => saveActiveAvatar(selected), "形象已切换，悬浮窗立即重载");
  };

  return (
    <div>
      <Section title="形象包" hint={`当前人设「${persona}」的形象组，切换后悬浮窗无需重启。`}>
        {settings.avatar.packs.length === 0 && (
          <p style={HINT_STYLE}>当前人设没有可用形象，悬浮窗显示占位。</p>
        )}
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
          {settings.avatar.packs.map((pack) => (
            <li key={pack.id}>
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  border: selected === pack.id ? "1px solid #3b82f6" : "1px solid #e5e7eb",
                  borderRadius: 8,
                  padding: 8,
                  fontSize: 13,
                  cursor: "pointer",
                }}
              >
                <input
                  type="radio"
                  name="avatar"
                  checked={selected === pack.id}
                  disabled={busy}
                  onChange={() => setSelected(pack.id)}
                />
                <PackPreview persona={persona} pack={pack} />
                <span>
                  <strong>{pack.name}</strong>
                  <small style={{ color: "#777", marginLeft: 8 }}>{pack.id}</small>
                  <br />
                  <small style={{ color: "#777" }}>{packStatesText(pack)}</small>
                </span>
              </label>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="保存">
        <SaveButton disabled={busy || !dirty} onSave={onSave} label="应用形象" />
        <StatusLine error={error} notice={notice} />
      </Section>
    </div>
  );
}
