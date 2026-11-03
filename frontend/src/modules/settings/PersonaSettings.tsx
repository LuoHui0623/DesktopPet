import { useCallback, useEffect, useState } from "react";
import type { PersonaItem, PersonasResponse } from "../../api/types";
import { describeRequestError } from "../../service/api-client";
import { loadPersonas, switchPersona } from "../../service/persona.service";
import { canActivate, personaStatusText } from "./persona-view";

const sectionStyle = { display: "grid", gap: 8, marginBottom: 20 } as const;
const headingStyle = { fontSize: 15, margin: 0 } as const;
const buttonStyle = { padding: "2px 10px", fontSize: 12 } as const;

export function PersonaSettings({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<PersonasResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(
    () =>
      loadPersonas().then(
        (next) => {
          setData(next);
          setLoadError(null);
        },
        (error: unknown) => {
          setLoadError(describeRequestError(error));
        },
      ),
    [],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const activate = (persona: PersonaItem) => {
    setBusy(true);
    setNotice(null);
    setActionError(null);
    switchPersona(persona.id)
      .then(
        (next) => {
          setData(next);
          setNotice(
            `已启用“${persona.name ?? persona.id}”。下一次对话起使用新人设，开始新会话，近期对话保留`,
          );
          onChanged?.();
        },
        (error: unknown) => {
          setActionError(describeRequestError(error));
        },
      )
      .finally(() => setBusy(false));
  };

  if (data === null) {
    return loadError ? (
      <p role="alert" style={{ color: "#991b1b" }}>
        {loadError}
      </p>
    ) : (
      <p style={{ color: "#777" }}>加载中…</p>
    );
  }

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
        {actionError && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {actionError}
          </p>
        )}
        {notice && <p style={{ color: "#166534", fontSize: 13, margin: 0 }}>{notice}</p>}
        {loadError && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {loadError}
          </p>
        )}
      </section>
    </div>
  );
}
