import { useEffect, useRef, useState, type RefObject } from "react";
import type { InteractionKind, PetState } from "../../api/types";
import type { LoadedAvatar } from "../../service/avatar.service";
import { loadAvatarAlpha, type AvatarAlpha } from "./avatar-alpha";
import { selectAvatarAnimation } from "./avatar-view";
import { centerOffset } from "./pet-alpha";
import { useAvatarFrame } from "./use-avatar-frame";

const RENDER_SIZE = 120;

/** 用窗口坐标判断是否落在形象的不透明像素上；返回 null 表示暂无轮廓信息。 */
export type PetHitTest = (clientX: number, clientY: number) => boolean;

const STATE_COLORS: Record<PetState, string> = {
  idle: "#8ec5ff",
  thinking: "#c3a6ff",
  speaking: "#ffd166",
  executing: "#7bd389",
  sleeping: "#9aa5b1",
};

const REACTIONS: Record<InteractionKind, string> = {
  click: "scale(1.12)",
  hover: "translateY(-3px) scale(1.05)",
  drag: "rotate(-4deg)",
  menu: "scale(0.95)",
};

function FallbackPet({ state }: { state: PetState | null }) {
  const color = state === null ? "transparent" : STATE_COLORS[state];
  return (
    <svg
      data-tauri-drag-region
      width="120"
      height="120"
      viewBox="0 0 120 120"
      role="img"
      aria-hidden="true"
    >
      <circle
        data-tauri-drag-region
        cx="60"
        cy="60"
        r="50"
        fill={color}
        stroke={state === null ? "#9aa5b1" : "#444"}
        strokeDasharray={state === null ? "6 6" : undefined}
        strokeWidth="3"
      />
      <circle data-tauri-drag-region cx="44" cy="52" r="5" fill="#333" />
      <circle data-tauri-drag-region cx="76" cy="52" r="5" fill="#333" />
      <path
        data-tauri-drag-region
        d="M42 74 Q60 88 78 74"
        fill="none"
        stroke="#333"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function PetView({
  state,
  avatar,
  reaction,
  hitTestRef,
}: {
  state: PetState | null;
  avatar: LoadedAvatar | null;
  reaction: InteractionKind | null;
  hitTestRef?: RefObject<PetHitTest | null>;
}) {
  const animation = avatar === null ? null : selectAvatarAnimation(avatar.pack, state);
  const frame = useAvatarFrame(animation);
  const src =
    animation === null || frame === undefined || avatar === null
      ? undefined
      : avatar.frameUrls.get(frame);
  const imageRef = useRef<HTMLImageElement>(null);
  const alphaRef = useRef<AvatarAlpha | null>(null);
  const [alignment, setAlignment] = useState<{ src: string; offset: number } | null>(null);
  const offset = alignment !== null && alignment.src === src ? alignment.offset : 0;

  useEffect(() => {
    alphaRef.current = null;
    if (src === undefined) return;
    let cancelled = false;
    const url = src;
    loadAvatarAlpha(url)
      .then((alpha) => {
        if (cancelled || alpha === null) return;
        alphaRef.current = alpha;
        setAlignment({ src: url, offset: centerOffset(alpha.bounds, alpha.width, RENDER_SIZE) });
      })
      .catch((error: unknown) => {
        console.error("读取形象轮廓失败", error);
      });
    return () => {
      cancelled = true;
    };
  }, [src]);

  useEffect(() => {
    if (hitTestRef === undefined) return;
    hitTestRef.current = (clientX, clientY) => {
      const image = imageRef.current;
      const alpha = alphaRef.current;
      // 还没有轮廓信息（首帧未解码或使用占位形象）时按整块命中，避免误吞点击。
      if (image === null || alpha === null) return true;
      const rect = image.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return true;
      if (
        clientX < rect.left ||
        clientX > rect.right ||
        clientY < rect.top ||
        clientY > rect.bottom
      ) {
        return false;
      }
      const x = Math.floor(((clientX - rect.left) / rect.width) * alpha.width);
      const y = Math.floor(((clientY - rect.top) / rect.height) * alpha.height);
      return alpha.isOpaqueAt(x, y);
    };
    return () => {
      hitTestRef.current = null;
    };
  }, [hitTestRef]);

  return (
    <div
      style={{
        width: RENDER_SIZE,
        height: RENDER_SIZE,
        flexShrink: 0,
        transform: reaction === null ? undefined : REACTIONS[reaction],
        transformOrigin: "50% 90%",
        transition: "transform 140ms ease-out",
      }}
    >
      <div style={{ transform: `translateX(${offset}px)` }}>
        {src === undefined ? (
          <FallbackPet state={state} />
        ) : (
          <img
            ref={imageRef}
            data-tauri-drag-region
            src={src}
            alt=""
            aria-hidden="true"
            draggable={false}
            width={RENDER_SIZE}
            height={RENDER_SIZE}
            style={{
              display: "block",
              width: RENDER_SIZE,
              height: RENDER_SIZE,
              objectFit: "contain",
            }}
          />
        )}
      </div>
    </div>
  );
}
