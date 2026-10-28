import { createStore } from "zustand/vanilla";
import type {
  ApprovalItem,
  ChatReplyResponse,
  MemorySourceRef,
  ServerEvent,
} from "../../api/types";

export type ChatRole = "user" | "assistant";
export type ChatMessageStatus = "done" | "pending" | "streaming" | "error";

export interface ChatMessage {
  localId: string;
  role: ChatRole;
  text: string;
  status: ChatMessageStatus;
  messageId: string | null;
  sources: MemorySourceRef[];
}

export interface PendingApproval extends ApprovalItem {
  planId: string;
}

export interface ChatStoreState {
  messages: ChatMessage[];
  sending: boolean;
  pendingLocalId: string | null;
  approvals: PendingApproval[];
  approvalSubmitting: string | null;
  approvalError: string | null;
  beginSend: (text: string) => string | null;
  completeSend: (localId: string, reply: ChatReplyResponse) => void;
  failSend: (localId: string, message: string) => void;
  beginApproval: (approvalId: string) => boolean;
  completeApproval: (approvalId: string) => void;
  failApproval: (approvalId: string, message: string) => void;
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
      approvals: [],
      approvalSubmitting: null,
      approvalError: null,
      beginSend: (text) => {
        const trimmed = text.trim();
        if (!trimmed || get().sending) return null;
        const userMessage: ChatMessage = {
          localId: nextLocalId(),
          role: "user",
          text: trimmed,
          status: "done",
          messageId: null,
          sources: [],
        };
        const replyMessage: ChatMessage = {
          localId: nextLocalId(),
          role: "assistant",
          text: "",
          status: "pending",
          messageId: null,
          sources: [],
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
              ? {
                  ...message,
                  text: reply.text,
                  status: "done",
                  messageId: reply.message_id,
                  sources: reply.sources,
                }
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
      beginApproval: (approvalId) => {
        const state = get();
        if (state.approvalSubmitting !== null) return false;
        if (!state.approvals.some((item) => item.approval_id === approvalId)) return false;
        set({ approvalSubmitting: approvalId, approvalError: null });
        return true;
      },
      completeApproval: (approvalId) => {
        set((state) => ({
          approvals: state.approvals.filter((item) => item.approval_id !== approvalId),
          approvalSubmitting:
            state.approvalSubmitting === approvalId ? null : state.approvalSubmitting,
          approvalError: null,
        }));
      },
      failApproval: (approvalId, message) => {
        set((state) => ({
          approvalSubmitting:
            state.approvalSubmitting === approvalId ? null : state.approvalSubmitting,
          approvalError: message,
        }));
      },
      applyEvent: (event) => {
        if (event.type === "approval.request") {
          set({
            approvals: event.items.map((item) => ({ ...item, planId: event.plan_id })),
            approvalSubmitting: null,
            approvalError: null,
          });
          return;
        }
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
          patchMessage(localId, { text: event.text, status: "done", sources: event.sources });
        }
      },
    };
  });
}

export const chatStore = createChatStore();
