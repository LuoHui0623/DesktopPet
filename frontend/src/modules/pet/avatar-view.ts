import type { AvatarPack, AvatarsResponse, PetState } from "../../api/types";

export const DEFAULT_AVATAR_PACK_ID = "default";
export const DISCONNECTED_AVATAR_STATE = "disconnected";

export interface AvatarAnimation {
  frames: readonly string[];
  frameMs: number;
}

// 依次尝试当前形象包与默认包；两者都不可用时返回空，由内联 SVG 兜底。
export function avatarCandidates(catalog: AvatarsResponse): AvatarPack[] {
  const byId = new Map(catalog.packs.map((pack) => [pack.id, pack]));
  const candidates: AvatarPack[] = [];
  for (const id of [catalog.active, DEFAULT_AVATAR_PACK_ID]) {
    const pack = byId.get(id);
    if (pack !== undefined && !candidates.includes(pack)) candidates.push(pack);
  }
  return candidates;
}

export function avatarStateKey(state: PetState | null): string {
  return state ?? DISCONNECTED_AVATAR_STATE;
}

export function selectAvatarAnimation(
  pack: AvatarPack,
  state: PetState | null,
): AvatarAnimation | null {
  const entry = pack.states[avatarStateKey(state)] ?? pack.states[pack.fallback];
  if (entry === undefined || entry.frames.length === 0) return null;
  return { frames: entry.frames, frameMs: entry.frame_ms };
}

export function avatarFrameAt(animation: AvatarAnimation, tick: number): string | undefined {
  return animation.frames[tick % animation.frames.length];
}

export function avatarFramePath(packId: string, frame: string): string {
  const encoded = frame.split("/").map(encodeURIComponent).join("/");
  return `/api/avatars/${encodeURIComponent(packId)}/${encoded}`;
}
