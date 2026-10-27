import { useState } from "react";
import { MemorySettings } from "./MemorySettings";
import { ModelSettings } from "./ModelSettings";
import { PersonaSettings } from "./PersonaSettings";

type Tab = "models" | "personas" | "memory";

const TABS: ReadonlyArray<{ key: Tab; label: string }> = [
  { key: "models", label: "模型" },
  { key: "personas", label: "人设" },
  { key: "memory", label: "记忆" },
];

export function SettingsWindow() {
  const [tab, setTab] = useState<Tab>("models");

  return (
    <div
      style={{
        height: "100vh",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        fontFamily: "system-ui, sans-serif",
        color: "#222",
        background: "#fff",
      }}
    >
      <nav role="tablist" style={{ display: "flex", borderBottom: "1px solid #ddd" }}>
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
            style={{
              flex: 1,
              padding: "8px 0",
              border: "none",
              background: "transparent",
              fontWeight: tab === item.key ? 600 : 400,
              borderBottom: tab === item.key ? "2px solid #3b82f6" : "2px solid transparent",
              cursor: "pointer",
            }}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 16 }}>
        {tab === "models" && <ModelSettings />}
        {tab === "personas" && <PersonaSettings />}
        {tab === "memory" && <MemorySettings />}
      </div>
    </div>
  );
}
