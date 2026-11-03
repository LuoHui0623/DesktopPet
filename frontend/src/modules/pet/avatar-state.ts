import type { PetState } from "../../api/types";
import type { AnimationResource } from "./animation-resource";

export const DISCONNECTED_AVATAR_STATE = "disconnected";

export interface AvatarStateTable {
  readonly presetStates: readonly string[];
  readonly fallbackState: string;
  readonly staticResource: AnimationResource | null;
  readonly animations: ReadonlyMap<string, AnimationResource>;
}

export interface AvatarSelection {
  animation: AnimationResource;
  source: "state" | "fallback" | "static";
}

export interface AvatarStateTableInput {
  presetStates: readonly string[];
  fallbackState: string;
  animations: ReadonlyMap<string, AnimationResource>;
  staticResource: AnimationResource | null;
}

/** 按后端 preset_states 顺序建表；未注册状态名不进入映射。 */
export function createAvatarStateTable(input: AvatarStateTableInput): AvatarStateTable {
  const allowed = new Set(input.presetStates);
  const animations = new Map<string, AnimationResource>();
  for (const key of input.presetStates) {
    const animation = input.animations.get(key);
    if (animation !== undefined) animations.set(key, animation);
  }
  for (const [key, animation] of input.animations) {
    if (allowed.has(key) && !animations.has(key)) animations.set(key, animation);
  }
  return {
    presetStates: [...input.presetStates],
    fallbackState: input.fallbackState,
    staticResource: input.staticResource,
    animations,
  };
}

export function avatarStateKey(state: string | null): string {
  return state ?? DISCONNECTED_AVATAR_STATE;
}

/**
 * 回退链：请求状态动画 → fallback 动画 → 静态主形象 → null（占位）。
 * 静态主形象不属于任何状态，只能作为最后一级兜底。
 */
export function selectAvatarAnimation(
  table: AvatarStateTable,
  stateKey: string,
): AvatarSelection | null {
  const direct = table.animations.get(stateKey);
  if (direct !== undefined) return { animation: direct, source: "state" };
  const fallback = table.animations.get(table.fallbackState);
  if (fallback !== undefined) return { animation: fallback, source: "fallback" };
  if (table.staticResource !== null) {
    return { animation: table.staticResource, source: "static" };
  }
  return null;
}

export type AvatarBehaviorContext = {
  connected: boolean;
  status: PetState | null;
  [kind: string]: unknown;
};

export type AvatarBehaviorResolver = (context: AvatarBehaviorContext) => string | null;

/** 内置解析：连接断开优先，其次聊天状态；与现有「连接 ? status : disconnected」一致。 */
export const builtinAvatarResolvers: readonly AvatarBehaviorResolver[] = [
  (context) => (context.connected ? null : DISCONNECTED_AVATAR_STATE),
  (context) => context.status ?? DISCONNECTED_AVATAR_STATE,
];

/** 按注册优先级取首个非空且已注册的状态；全部为空时保持上一次解析结果。 */
export function createAvatarBehaviorResolver(
  resolvers: readonly AvatarBehaviorResolver[] = builtinAvatarResolvers,
  presetStates: readonly string[] = [],
): (context: AvatarBehaviorContext) => string | null {
  const allowed = new Set(presetStates);
  let last: string | null = null;
  return (context) => {
    for (const resolver of resolvers) {
      const key = resolver(context);
      if (key === null) continue;
      if (!allowed.has(key)) continue;
      last = key;
      return key;
    }
    return last;
  };
}
