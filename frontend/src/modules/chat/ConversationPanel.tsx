import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useStore } from "zustand";
import { sendMessage } from "../../service/chat.service";
import { noticeLabels } from "../../service/notices";
import { chatStore } from "../../service/stores/chat.store";
import { ApprovalCard } from "./ApprovalCard";
import { MessageBubble } from "./MessageBubble";

export function ConversationPanel() {
  const messages = useStore(chatStore, (state) => state.messages);
  const sending = useStore(chatStore, (state) => state.sending);
  const approvals = useStore(chatStore, (state) => state.approvals);
  const approvalSubmitting = useStore(chatStore, (state) => state.approvalSubmitting);
  const approvalError = useStore(chatStore, (state) => state.approvalError);
  const notices = useStore(chatStore, (state) => state.notices);
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages]);

  const canSend = !sending && draft.trim().length > 0;
  const noticeTexts = noticeLabels(notices);

  const submit = () => {
    if (!canSend) return;
    const text = draft;
    setDraft("");
    void sendMessage(text);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <section style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <div
        ref={listRef}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          padding: 12,
        }}
      >
        {messages.length === 0 ? (
          <p style={{ color: "#777", fontSize: 13, margin: "auto" }}>发送一条消息开始对话</p>
        ) : (
          messages.map((message) => <MessageBubble key={message.localId} message={message} />)
        )}
      </div>
      {noticeTexts.length > 0 ? (
        <p
          role="status"
          aria-live="polite"
          style={{
            margin: 0,
            padding: "6px 12px",
            borderTop: "1px solid #f2e2c4",
            background: "#fff7e6",
            color: "#8a6d3b",
            fontSize: 12,
          }}
        >
          {noticeTexts.join("；")}
        </p>
      ) : null}
      {approvals.length > 0 ? (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            padding: 12,
            borderTop: "1px solid #ddd",
            maxHeight: "40%",
            overflowY: "auto",
          }}
        >
          {approvals.map((approval) => (
            <ApprovalCard
              key={approval.approval_id}
              approval={approval}
              busy={approvalSubmitting !== null}
            />
          ))}
          {approvalError ? (
            <p style={{ color: "#c0392b", fontSize: 12, margin: 0 }}>{approvalError}</p>
          ) : null}
        </div>
      ) : null}
      <div style={{ display: "flex", gap: 8, padding: 12, borderTop: "1px solid #ddd" }}>
        <textarea
          aria-label="输入消息"
          value={draft}
          rows={2}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          style={{ flex: 1, resize: "none", fontSize: 14, padding: 6 }}
        />
        <button type="button" onClick={submit} disabled={!canSend} style={{ padding: "0 14px" }}>
          发送
        </button>
      </div>
    </section>
  );
}
