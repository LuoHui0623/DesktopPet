import { createStore } from "zustand/vanilla";
import type { ChatReplyResponse, ServerEvent } from "../../api/types";

export type ChatRole = "user" | "assistant";
export type ChatMessageStatus = "done" | "pending" | "streaming" | "error";

export interface ChatMessage {
  localId: string;
  role: ChatRole;
  text: string;
  status: ChatMessageStatus;
  messageId: string | null;
}

export interface ChatStoreState {
  messages: ChatMessage[];
  sending: boolean;
  pendingLocalId: string | null;
  beginSend: (text: string) => string | null;
  completeSend: (localId: string, reply: ChatReplyResponse) => void;
  failSend: (localId: string, message: string) => void;
  applyEvent: (event: ServerEvent) => void;
}

export function createChatStore() {
  let sequence = 0;
  const nextLocalId = (): string => {
    sequence += 1;
    return `local_${sequence}`;
  };

  return createStore<ChatStoreState>()((set, get) => {
    const patchMessage = (localId: string, patch: Partial<ChatMessage>): void => {
      set((state) => ({
        messages: state.messages.map((message) =>
          message.localId === localId ? { ...message, ...patch } : message,
        ),
      }));
    };

    // 事件里的 message_id 可能先于 HTTP 返回到达，此时把等待中的回复占位绑定到该 id。
    const resolveTarget = (messageId: string): string | null => {
      const bound = get().messages.find((message) => message.messageId === messageId);
      if (bound) return bound.localId;
      const pending = get().messages.find(
        (message) => message.localId === get().pendingLocalId && message.messageId === null,
      );
      if (!pending) return null;
      patchMessage(pending.localId, { messageId });
      return pending.localId;
    };

    return {
      messages: [],
      sending: false,
      pendingLocalId: null,
      beginSend: (text) => {
        const trimmed = text.trim();
        if (!trimmed || get().sending) return null;
        const userMessage: ChatMessage = {
          localId: nextLocalId(),
          role: "user",
          text: trimmed,
          status: "done",
          messageId: null,
        };
        const replyMessage: ChatMessage = {
          localId: nextLocalId(),
          role: "assistant",
          text: "",
          status: "pending",
          messageId: null,
        };
        set((state) => ({
          messages: [...state.messages, userMessage, replyMessage],
          sending: true,
          pendingLocalId: replyMessage.localId,
        }));
        return replyMessage.localId;
      },
      completeSend: (localId, reply) => {
        set((state) => ({
          messages: state.messages.map((message) =>
            message.localId === localId
              ? { ...message, text: reply.text, status: "done", messageId: reply.message_id }
              : message,
          ),
          sending: false,
          pendingLocalId: null,
        }));
      },
      failSend: (localId, message) => {
        set((state) => ({
          messages: state.messages.map((item) =>
            item.localId === localId ? { ...item, text: message, status: "error" } : item,
          ),
          sending: false,
          pendingLocalId: null,
        }));
      },
      applyEvent: (event) => {
        if (event.type === "chat.chunk") {
          const localId = resolveTarget(event.message_id);
          if (!localId) return;
          // 只在回复尚未完成时追加；完成后以完整文本为准，避免重复。
          set((state) => ({
            messages: state.messages.map((message) =>
              message.localId === localId &&
              (message.status === "pending" || message.status === "streaming")
                ? { ...message, text: message.text + event.text, status: "streaming" }
                : message,
            ),
          }));
        } else if (event.type === "chat.done") {
          const localId = resolveTarget(event.message_id);
          if (!localId) return;
          patchMessage(localId, { text: event.text, status: "done" });
        }
      },
    };
  });
}

export const chatStore = createChatStore();
