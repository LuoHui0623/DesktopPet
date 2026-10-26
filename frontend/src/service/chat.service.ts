import { invoke } from "@tauri-apps/api/core";
import { ApiError, createApiClient, type ApiClient, type BackendInfo } from "../api/client";
import { listChatHistory, sendChat } from "../api/endpoints";
import type { ChatHistoryQuery, ChatHistoryResponse } from "../api/types";
import { GET_BACKEND_INFO_COMMAND } from "./backend.service";
import { chatStore, type ChatStoreState } from "./stores/chat.store";

export type ChatStoreApi = { getState: () => ChatStoreState };

let clientPromise: Promise<ApiClient> | null = null;

function getClient(): Promise<ApiClient> {
  clientPromise ??= invoke<BackendInfo>(GET_BACKEND_INFO_COMMAND).then(
    (info) => createApiClient(info),
    (error: unknown) => {
      clientPromise = null;
      throw error;
    },
  );
  return clientPromise;
}

export function describeRequestError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "无法连接后端，请稍后重试";
}

export async function sendMessage(text: string, store: ChatStoreApi = chatStore): Promise<void> {
  const trimmed = text.trim();
  const localId = store.getState().beginSend(trimmed);
  if (localId === null) return;
  try {
    const client = await getClient();
    const reply = await sendChat(client, trimmed);
    store.getState().completeSend(localId, reply);
  } catch (error: unknown) {
    store.getState().failSend(localId, describeRequestError(error));
  }
}

export async function searchHistory(query: ChatHistoryQuery): Promise<ChatHistoryResponse> {
  const client = await getClient();
  return listChatHistory(client, query);
}
