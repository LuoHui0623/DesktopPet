import type { ChatMessage } from "../../service/stores/chat.store";
import { roleStamp } from "./clock-view";
import { MemorySources } from "./MemorySources";

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const isError = message.status === "error";
  const waiting = message.status === "pending";
  const text = waiting ? "…" : message.text;

  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div style={{ maxWidth: "80%", display: "grid", gap: 6 }}>
        <div style={{ color: "#888", fontSize: 11, textAlign: isUser ? "right" : "left" }}>
          {roleStamp(isUser ? "master" : "me", message.at)}
        </div>
        <div
          role={isError ? "alert" : undefined}
          style={{
            padding: "6px 10px",
            borderRadius: 10,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontSize: 14,
            lineHeight: 1.5,
            background: isUser ? "#dbeafe" : isError ? "#fee2e2" : "#f3f4f6",
            color: isError ? "#991b1b" : "#222",
          }}
        >
          {text}
        </div>
        {!isUser && message.status === "done" && <MemorySources sources={message.sources} />}
      </div>
    </div>
  );
}
