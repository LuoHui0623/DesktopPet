import type { PersonaItem, SettingsView } from "../../api/types";
import { saveActivePersona } from "../../service/settings.service";
import { canActivate, personaStatusText } from "./persona-view";
import { useSettingsSave } from "./use-settings-save";

const sectionStyle = { display: "grid", gap: 8, marginBottom: 20 } as const;
const headingStyle = { fontSize: 15, margin: 0 } as const;
const buttonStyle = { padding: "2px 10px", fontSize: 12 } as const;

export function PersonaSettings({
  settings,
  onSaved,
}: {
  settings: SettingsView;
  onSaved: (view: SettingsView) => void;
}) {
  const { busy, error, notice, save } = useSettingsSave(settings.version, onSaved);
  const data = settings.persona;

  const activate = (persona: PersonaItem) => {
    save(
      (version) => saveActivePersona(persona.id, version),
      `已启用“${persona.name ?? persona.id}”。下一次对话起使用新人设，开始新会话，近期对话保留`,
    );
  };

  return (
    <div>
      <section style={sectionStyle}>
        <h2 style={headingStyle}>人设卡片</h2>
        <p style={{ margin: 0, fontSize: 13, color: "#555" }}>
          人设内容在 <code>backend/config/personas/&lt;id&gt;.yaml</code>{" "}
          中修改，保存后下一次对话自动生效。这里只选择启用哪一张。
        </p>
        {data.personas.length === 0 && (
          <p style={{ color: "#777", fontSize: 13 }}>未找到人设卡片</p>
        )}
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
          {data.personas.map((persona) => (
            <li
              key={persona.id}
              style={{
                border: `1px solid ${persona.active ? "#3b82f6" : "#e5e7eb"}`,
                borderRadius: 8,
                padding: 10,
                fontSize: 13,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <strong>
                  {persona.name ?? persona.id}
                  <span style={{ color: "#777", fontWeight: 400, marginLeft: 6 }}>
                    {persona.id}
                  </span>
                </strong>
                <span style={{ color: persona.available ? "#166534" : "#991b1b" }}>
                  {personaStatusText(persona)}
                </span>
              </div>
              {!persona.available && persona.reason && (
                <p role="alert" style={{ color: "#991b1b", margin: "4px 0 0" }}>
                  原因：{persona.reason}
                </p>
              )}
              <div style={{ marginTop: 6 }}>
                <button
                  type="button"
                  onClick={() => activate(persona)}
                  disabled={busy || !canActivate(persona)}
                  style={buttonStyle}
                >
                  启用
                </button>
              </div>
            </li>
          ))}
        </ul>
        {error && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {error}
          </p>
        )}
        {notice && <p style={{ color: "#166534", fontSize: 13, margin: 0 }}>{notice}</p>}
      </section>
    </div>
  );
}
