import { useState } from "react";
import { ConversationPanel } from "./ConversationPanel";
import { HistoryPanel } from "./HistoryPanel";

type Tab = "conversation" | "history";

const TABS: ReadonlyArray<{ key: Tab; label: string }> = [
  { key: "conversation", label: "对话" },
  { key: "history", label: "历史" },
];

export function ChatWindow() {
  const [tab, setTab] = useState<Tab>("conversation");

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
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
      {tab === "conversation" ? <ConversationPanel /> : <HistoryPanel />}
    </div>
  );
}
