import { listMemories, saveStructuredMemory } from "../api/endpoints";
import type { MemoriesQuery, MemoriesResponse, StructuredMemoryUpdate } from "../api/types";
import { getApiClient } from "./api-client";

export function loadMemories(query: MemoriesQuery = {}): Promise<MemoriesResponse> {
  return getApiClient().then((client) => listMemories(client, query));
}

export function saveStructured(text: string): Promise<StructuredMemoryUpdate> {
  return getApiClient().then((client) => saveStructuredMemory(client, text));
}
