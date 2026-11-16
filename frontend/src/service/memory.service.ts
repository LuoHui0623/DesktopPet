import {
  createMemoryEntry,
  disableMemoryEntry,
  enableMemoryEntry,
  listMemories,
  listMemoryEntries,
  listMemoryTags,
  mergeMemoryTags,
  previewTagMerge,
  updateMemoryEntry,
} from "../api/endpoints";
import type {
  EntryAddResult,
  EntryListResponse,
  EntryUpdateRequest,
  MemoriesQuery,
  MemoriesResponse,
  MemoryEntriesQuery,
  MemoryEntryCreate,
  MemoryEntryView,
  TagListResponse,
  TagMergePreview,
  TagMergeRequest,
  TagMergeResult,
} from "../api/types";
import { getApiClient } from "./api-client";

export function loadMemories(query: MemoriesQuery = {}): Promise<MemoriesResponse> {
  return getApiClient().then((client) => listMemories(client, query));
}

export function loadEntries(query: MemoryEntriesQuery = {}): Promise<EntryListResponse> {
  return getApiClient().then((client) => listMemoryEntries(client, query));
}

export function createEntry(payload: MemoryEntryCreate): Promise<EntryAddResult> {
  return getApiClient().then((client) => createMemoryEntry(client, payload));
}

export function updateEntry(
  entryId: number,
  payload: EntryUpdateRequest,
): Promise<MemoryEntryView> {
  return getApiClient().then((client) => updateMemoryEntry(client, entryId, payload));
}

export function enableEntry(entryId: number): Promise<MemoryEntryView> {
  return getApiClient().then((client) => enableMemoryEntry(client, entryId));
}

export function disableEntry(entryId: number, reason: string): Promise<MemoryEntryView> {
  return getApiClient().then((client) => disableMemoryEntry(client, entryId, reason));
}

export function loadTags(): Promise<TagListResponse> {
  return getApiClient().then((client) => listMemoryTags(client));
}

export function previewMerge(payload: TagMergeRequest): Promise<TagMergePreview> {
  return getApiClient().then((client) => previewTagMerge(client, payload));
}

export function mergeTags(payload: TagMergeRequest): Promise<TagMergeResult> {
  return getApiClient().then((client) => mergeMemoryTags(client, payload));
}
