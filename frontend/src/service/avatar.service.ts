import { TOKEN_HEADER, buildUrl, type ApiClient } from "../api/client";
import { listAvatars } from "../api/endpoints";
import type { AvatarPack } from "../api/types";
import { avatarCandidates, avatarFramePath } from "../modules/pet/avatar-view";
import { getApiClient } from "./api-client";

export interface LoadedAvatar {
  pack: AvatarPack;
  frameUrls: ReadonlyMap<string, string>;
}

async function fetchFrameUrls(client: ApiClient, pack: AvatarPack): Promise<Map<string, string>> {
  const frames = [...new Set(Object.values(pack.states).flatMap((state) => state.frames))];
  const entries = await Promise.all(
    frames.map(async (frame): Promise<[string, string]> => {
      const response = await client.fetch(buildUrl(client, avatarFramePath(pack.id, frame)), {
        headers: { [TOKEN_HEADER]: client.token },
      });
      if (!response.ok) throw new Error(`形象帧加载失败：${pack.id}/${frame}`);
      return [frame, URL.createObjectURL(await response.blob())];
    }),
  );
  return new Map(entries);
}

// 预解码全部帧，状态切换换 src 时不出现空白帧。
async function decodeFrames(urls: Iterable<string>): Promise<void> {
  if (typeof Image === "undefined") return;
  await Promise.all(
    [...urls].map((url) => {
      const image = new Image();
      image.src = url;
      return image.decode().catch(() => undefined);
    }),
  );
}

export async function loadAvatar(client?: ApiClient): Promise<LoadedAvatar | null> {
  const api = client ?? (await getApiClient());
  const catalog = await listAvatars(api);
  for (const pack of avatarCandidates(catalog)) {
    let frameUrls: Map<string, string>;
    try {
      frameUrls = await fetchFrameUrls(api, pack);
    } catch (error) {
      console.error(`形象包 ${pack.id} 加载失败`, error);
      continue;
    }
    await decodeFrames(frameUrls.values());
    return { pack, frameUrls };
  }
  return null;
}

export function releaseAvatar(avatar: LoadedAvatar): void {
  for (const url of avatar.frameUrls.values()) URL.revokeObjectURL(url);
}

// 设置页预览用：按帧路径取回对象 URL，调用方负责 revokeObjectURL。
export async function loadAvatarFrameUrl(
  packId: string,
  frame: string,
  client?: ApiClient,
): Promise<string> {
  const api = client ?? (await getApiClient());
  const response = await api.fetch(buildUrl(api, avatarFramePath(packId, frame)), {
    headers: { [TOKEN_HEADER]: api.token },
  });
  if (!response.ok) throw new Error(`形象帧加载失败：${packId}/${frame}`);
  return URL.createObjectURL(await response.blob());
}
