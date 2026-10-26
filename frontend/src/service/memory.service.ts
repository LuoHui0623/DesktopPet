import {
  addCoreMemory,
  deleteMemory,
  listMemories,
  removeCoreMemory,
  reorderCoreMemory,
  setMemoryTemporaryMode,
  updateMemory,
} from "../api/endpoints";
import type {
  CoreMemoriesResponse,
  DeleteMemoryResponse,
  MemoriesQuery,
  MemoriesResponse,
  MemoryItem,
  MemoryPatch,
  TemporaryModeResponse,
} from "../api/types";
import { getApiClient } from "./api-client";

export function loadMemories(query: MemoriesQuery = {}): Promise<MemoriesResponse> {
  return getApiClient().then((client) => listMemories(client, query));
}

export function editMemory(memoryId: string, patch: MemoryPatch): Promise<MemoryItem> {
  return getApiClient().then((client) => updateMemory(client, memoryId, patch));
}

export function removeMemory(memoryId: string): Promise<DeleteMemoryResponse> {
  return getApiClient().then((client) => deleteMemory(client, memoryId));
}

export function addToCore(memoryId: string): Promise<CoreMemoriesResponse> {
  return getApiClient().then((client) => addCoreMemory(client, memoryId));
}

export function removeFromCore(memoryId: string): Promise<CoreMemoriesResponse> {
  return getApiClient().then((client) => removeCoreMemory(client, memoryId));
}

export function reorderCore(memoryIds: string[]): Promise<CoreMemoriesResponse> {
  return getApiClient().then((client) => reorderCoreMemory(client, memoryIds));
}

export function setTemporaryMode(enabled: boolean): Promise<TemporaryModeResponse> {
  return getApiClient().then((client) => setMemoryTemporaryMode(client, enabled));
}
