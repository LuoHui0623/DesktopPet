import { TOKEN_HEADER, buildUrl, type ApiClient } from "../api/client";
import { listAvatars } from "../api/endpoints";
import type { AvatarPack } from "../api/types";
import {
  buildGifAnimation,
  buildSequenceAnimation,
  buildStaticAnimation,
  type AnimationResource,
  type RawAnimationFrame,
} from "../modules/pet/animation-resource";
import { createAvatarStateTable, type AvatarStateTable } from "../modules/pet/avatar-state";
import { avatarCandidates, avatarFramePath } from "../modules/pet/avatar-view";
import {
  createBrowserAvatarDecoder,
  type AvatarDecoder,
  type DecodedAssetFrame,
} from "../modules/pet/avatar-decode";
import { getApiClient } from "./api-client";

export interface LoadedAvatar {
  pack: AvatarPack;
  presetStates: readonly string[];
  staticResource: AnimationResource | null;
  table: AvatarStateTable;
  animations: ReadonlyMap<string, AnimationResource>;
}

export interface LoadAvatarOptions {
  onStatic?: (preview: LoadedAvatar) => void;
  decoder?: AvatarDecoder;
}

type DecodeCache = Map<string, Promise<readonly DecodedAssetFrame[]>>;

async function fetchAsset(
  client: ApiClient,
  personaId: string,
  packId: string,
  source: string,
): Promise<Blob> {
  const response = await client.fetch(
    buildUrl(client, avatarFramePath(personaId, packId, source)),
    {
      headers: { [TOKEN_HEADER]: client.token },
    },
  );
  if (!response.ok) throw new Error(`形象资源加载失败：${personaId}/${packId}/${source}`);
  return response.blob();
}

function decodeSource(
  decoder: AvatarDecoder,
  client: ApiClient,
  personaId: string,
  packId: string,
  source: string,
  cache: DecodeCache,
): Promise<readonly DecodedAssetFrame[]> {
  const key = `${personaId}/${packId}/${source}`;
  const cached = cache.get(key);
  if (cached !== undefined) return cached;
  const loading = fetchAsset(client, personaId, packId, source).then((blob) =>
    decoder.decode(source, blob),
  );
  cache.set(key, loading);
  return loading;
}

function makeTable(
  pack: AvatarPack,
  presetStates: readonly string[],
  staticResource: AnimationResource | null,
  animations: ReadonlyMap<string, AnimationResource>,
): AvatarStateTable {
  return createAvatarStateTable({
    presetStates,
    fallbackState: pack.fallback,
    animations,
    staticResource,
  });
}

async function loadStatic(
  decoder: AvatarDecoder,
  client: ApiClient,
  personaId: string,
  pack: AvatarPack,
  cache: DecodeCache,
): Promise<AnimationResource> {
  const decoded = await decodeSource(decoder, client, personaId, pack.id, pack.static, cache);
  const frame = decoded[0];
  if (frame === undefined) throw new Error(`主形象解码为空：${pack.id}/${pack.static}`);
  return buildStaticAnimation(pack.id, pack.static, pack.frame, {
    image: frame.image,
    durationMs: 0,
    mask: frame.mask,
  });
}

function framesForState(
  pack: AvatarPack,
  stateFrames: readonly string[],
  frameMs: number,
  decoded: readonly (readonly DecodedAssetFrame[])[],
): RawAnimationFrame[] {
  if (stateFrames.length === 1 && stateFrames[0].toLowerCase().endsWith(".gif")) {
    return (decoded[0] ?? []).map((frame) => ({
      image: frame.image,
      durationMs: frame.durationMs,
      mask: frame.mask,
      source: stateFrames[0],
    }));
  }
  return decoded.map((frames, index) => {
    const frame = frames[0];
    if (frame === undefined) throw new Error(`帧解码为空：${pack.id}/${stateFrames[index]}`);
    return {
      image: frame.image,
      durationMs: frameMs,
      mask: frame.mask,
      source: stateFrames[index],
    };
  });
}

async function loadAnimations(
  decoder: AvatarDecoder,
  client: ApiClient,
  personaId: string,
  pack: AvatarPack,
  presetStates: readonly string[],
  cache: DecodeCache,
): Promise<Map<string, AnimationResource>> {
  const allowed = new Set(presetStates);
  const animations = new Map<string, AnimationResource>();
  for (const [key, state] of Object.entries(pack.states)) {
    if (!allowed.has(key)) continue;
    try {
      const decoded = await Promise.all(
        state.frames.map((source) =>
          decodeSource(decoder, client, personaId, pack.id, source, cache),
        ),
      );
      const frames = framesForState(pack, state.frames, state.frame_ms, decoded);
      if (frames.length === 0) throw new Error("没有可用帧");
      const isGif = state.frames.length === 1 && state.frames[0].toLowerCase().endsWith(".gif");
      animations.set(
        key,
        isGif
          ? buildGifAnimation(pack.id, state.frames[0], pack.frame, frames)
          : buildSequenceAnimation(pack.id, state.frames.join(","), pack.frame, frames),
      );
    } catch (error: unknown) {
      console.error(`状态 ${key} 的动画加载失败`, error);
    }
  }
  return animations;
}

export async function loadAvatar(
  client?: ApiClient,
  options: LoadAvatarOptions = {},
): Promise<LoadedAvatar | null> {
  const api = client ?? (await getApiClient());
  const decoder = options.decoder ?? createBrowserAvatarDecoder();
  const catalog = await listAvatars(api);
  const personaId = catalog.persona;
  const presetStates = catalog.preset_states;
  for (const pack of avatarCandidates(catalog)) {
    const cache: DecodeCache = new Map();
    let staticResource: AnimationResource;
    try {
      staticResource = await loadStatic(decoder, api, personaId, pack, cache);
    } catch (error: unknown) {
      console.error(`形象包 ${pack.id} 的主形象加载失败`, error);
      continue;
    }
    const preview: LoadedAvatar = {
      pack,
      presetStates,
      staticResource,
      table: makeTable(pack, presetStates, staticResource, new Map()),
      animations: new Map(),
    };
    options.onStatic?.(preview);
    const animations = await loadAnimations(decoder, api, personaId, pack, presetStates, cache);
    return {
      pack,
      presetStates,
      staticResource,
      table: makeTable(pack, presetStates, staticResource, animations),
      animations,
    };
  }
  return null;
}

export function releaseAvatar(avatar: LoadedAvatar): void {
  const images = new Set<CanvasImageSource>();
  const resources = [avatar.staticResource, ...avatar.animations.values()];
  for (const resource of resources) {
    if (resource === null) continue;
    for (const frame of resource.frames) {
      if (frame.image !== null) images.add(frame.image);
    }
  }
  for (const image of images) {
    const closeable = image as { close?: () => void };
    closeable.close?.();
  }
}

// 设置页预览用：按人设与包取回帧对象 URL，调用方负责 revokeObjectURL。
export async function loadAvatarFrameUrl(
  personaId: string,
  packId: string,
  frame: string,
  client?: ApiClient,
): Promise<string> {
  const api = client ?? (await getApiClient());
  const response = await api.fetch(buildUrl(api, avatarFramePath(personaId, packId, frame)), {
    headers: { [TOKEN_HEADER]: api.token },
  });
  if (!response.ok) throw new Error(`形象帧加载失败：${personaId}/${packId}/${frame}`);
  return URL.createObjectURL(await response.blob());
}
