import { invoke } from "@tauri-apps/api/core";
import { ApiError, createApiClient, type ApiClient, type BackendInfo } from "../api/client";
import { GET_BACKEND_INFO_COMMAND } from "./backend.service";

let clientPromise: Promise<ApiClient> | null = null;

export function getApiClient(): Promise<ApiClient> {
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
