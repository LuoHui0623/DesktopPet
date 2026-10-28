import { sendChat, listChatHistory, submitApproval } from "../api/endpoints";
import type { ChatHistoryQuery, ChatHistoryResponse } from "../api/types";
import { describeRequestError, getApiClient } from "./api-client";
import { chatStore, type ChatStoreState } from "./stores/chat.store";

export type ChatStoreApi = { getState: () => ChatStoreState };

export async function sendMessage(text: string, store: ChatStoreApi = chatStore): Promise<void> {
  const trimmed = text.trim();
  const localId = store.getState().beginSend(trimmed);
  if (localId === null) return;
  try {
    const client = await getApiClient();
    const reply = await sendChat(client, trimmed);
    store.getState().completeSend(localId, reply);
  } catch (error: unknown) {
    store.getState().failSend(localId, describeRequestError(error));
  }
}

export async function searchHistory(query: ChatHistoryQuery): Promise<ChatHistoryResponse> {
  const client = await getApiClient();
  return listChatHistory(client, query);
}

export async function decideApproval(
  approvalId: string,
  approved: boolean,
  store: ChatStoreApi = chatStore,
): Promise<void> {
  if (!store.getState().beginApproval(approvalId)) return;
  try {
    const client = await getApiClient();
    await submitApproval(client, { approval_id: approvalId, approved });
    store.getState().completeApproval(approvalId);
  } catch (error: unknown) {
    store.getState().failApproval(approvalId, describeRequestError(error));
  }
}
