import type { ChatMessage } from "../../service/stores/chat.store";

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const isError = message.status === "error";
  const waiting = message.status === "pending";
  const text = waiting ? "…" : message.text;

  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div
        role={isError ? "alert" : undefined}
        style={{
          maxWidth: "80%",
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
    </div>
  );
}
