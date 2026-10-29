import { useState } from "react";
import { AvatarSettings } from "./AvatarSettings";
import { InteractionSettings } from "./InteractionSettings";
import { MemorySettings } from "./MemorySettings";
import { ModelSettings } from "./ModelSettings";
import { PerceptionSettings } from "./PerceptionSettings";
import { PersonaSettings } from "./PersonaSettings";
import { ProactiveSettings } from "./ProactiveSettings";
import { useSettingsBundle } from "./use-settings-bundle";

type Tab = "models" | "personas" | "memory" | "perception" | "proactive" | "avatar" | "interaction";

const TABS: ReadonlyArray<{ key: Tab; label: string }> = [
  { key: "models", label: "模型" },
  { key: "personas", label: "人设" },
  { key: "memory", label: "记忆" },
  { key: "perception", label: "感知" },
  { key: "proactive", label: "主动消息" },
  { key: "avatar", label: "形象" },
  { key: "interaction", label: "互动" },
];

// 本期新增的四组配置统一走 GET/PUT /api/settings/*，真源是 app_settings。
const MANAGED_TABS: ReadonlySet<Tab> = new Set<Tab>([
  "perception",
  "proactive",
  "avatar",
  "interaction",
]);

export function SettingsWindow() {
  const [tab, setTab] = useState<Tab>("models");
  const bundle = useSettingsBundle();

  const renderManaged = () => {
    if (bundle.view === null) {
      return (
        <div>
          {bundle.error !== null && (
            <p role="alert" style={{ color: "#991b1b", fontSize: 13 }}>
              {bundle.error}
            </p>
          )}
          <p style={{ color: "#777", fontSize: 13 }}>加载中…</p>
          <button type="button" onClick={bundle.reload}>
            重新读取
          </button>
        </div>
      );
    }
    if (tab === "perception") {
      return (
        <PerceptionSettings
          settings={bundle.view}
          perceptionState={bundle.perceptionState}
          onSaved={bundle.apply}
        />
      );
    }
    if (tab === "proactive") {
      return <ProactiveSettings settings={bundle.view} onSaved={bundle.apply} />;
    }
    if (tab === "avatar") {
      return <AvatarSettings settings={bundle.view} onSaved={bundle.apply} />;
    }
    return <InteractionSettings settings={bundle.view} onSaved={bundle.apply} />;
  };

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
      <nav
        role="tablist"
        style={{
          display: "flex",
          flexWrap: "wrap",
          borderBottom: "1px solid #ddd",
        }}
      >
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
            style={{
              padding: "8px 12px",
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
        {MANAGED_TABS.has(tab) && renderManaged()}
      </div>
    </div>
  );
}
