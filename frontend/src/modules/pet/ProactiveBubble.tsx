import { noticeLabels } from "../../service/notices";
import type { ProactiveMessage } from "../../service/stores/pet.store";

interface ProactiveBubbleProps {
  message: ProactiveMessage;
  onOpen: () => void;
  onIgnore: () => void;
}

export function ProactiveBubble({ message, onOpen, onIgnore }: ProactiveBubbleProps) {
  return (
    <section
      role="button"
      tabIndex={0}
      aria-label={`主动消息：${message.text}`}
      onClick={(event) => {
        event.stopPropagation();
        onOpen();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          event.stopPropagation();
          onOpen();
        }
      }}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 6,
        padding: "8px 10px",
        borderRadius: 12,
        background: "rgba(255,255,255,0.97)",
        boxShadow: "0 6px 18px rgba(0,0,0,0.16)",
        fontSize: 13,
        lineHeight: 1.4,
        cursor: "pointer",
      }}
    >
      <div style={{ flex: 1, display: "grid", gap: 2 }}>
        <p style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{message.text}</p>
        {noticeLabels(message.notices).map((text) => (
          <span key={text} style={{ color: "#8a6d3b", fontSize: 11 }}>
            {text}
          </span>
        ))}
      </div>
      <button
        type="button"
        aria-label="忽略本会话的主动消息"
        onClick={(event) => {
          event.stopPropagation();
          onIgnore();
        }}
        onKeyDown={(event) => event.stopPropagation()}
        style={{
          flexShrink: 0,
          padding: "0 4px",
          border: "none",
          background: "transparent",
          color: "#666",
          fontSize: 12,
          cursor: "pointer",
        }}
      >
        忽略
      </button>
    </section>
  );
}
