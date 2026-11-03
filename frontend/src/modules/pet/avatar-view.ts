import type { AvatarPack, AvatarsResponse } from "../../api/types";

export const DEFAULT_AVATAR_PACK_ID = "default";

// 依次尝试当前形象包与组内默认包；两者都不可用时返回空，由内联占位兜底。
export function avatarCandidates(catalog: AvatarsResponse): AvatarPack[] {
  const byId = new Map(catalog.packs.map((pack) => [pack.id, pack]));
  const candidates: AvatarPack[] = [];
  for (const id of [catalog.active, DEFAULT_AVATAR_PACK_ID]) {
    if (id === null) continue;
    const pack = byId.get(id);
    if (pack !== undefined && !candidates.includes(pack)) candidates.push(pack);
  }
  return candidates;
}

export function avatarFramePath(personaId: string, packId: string, frame: string): string {
  const encoded = frame.split("/").map(encodeURIComponent).join("/");
  return `/api/avatars/${encodeURIComponent(personaId)}/${encodeURIComponent(packId)}/${encoded}`;
}
