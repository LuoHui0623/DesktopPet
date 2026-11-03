import { useCallback, useEffect, useMemo, useRef, type RefObject } from "react";
import type { InteractionKind } from "../../api/types";
import type { LoadedAvatar } from "../../service/avatar.service";
import type { AnimationResource } from "./animation-resource";
import { containRect, offsetRect, transformOrigin, type Point } from "./avatar-layout";
import { isAvatarPixel, type FrameViewport } from "./avatar-hit";
import { avatarStateKey, selectAvatarAnimation } from "./avatar-state";
import { useAvatarFrame } from "./use-avatar-frame";

const RENDER_SIZE = 120;

/** 用窗口坐标判断是否落在形象的不透明像素上；返回 null 表示暂无轮廓信息。 */
export type PetHitTest = (clientX: number, clientY: number) => boolean;

/** 供穿透轮询读取当前帧掩码与绘制矩形；坐标均为窗口内 CSS 坐标。 */
export type PetFrameProvider = () => FrameViewport | null;

/**
 * 画布在窗口内的未变换原点：反应动画的 transform 不计入，才能与绘制矩形的局部坐标对齐。
 * 以 offsetParent（窗口根容器）的视口位置加 offsetLeft/Top 得到，offsetLeft/Top 不受 transform 影响。
 */
function untransformedOrigin(element: HTMLElement): Point {
  const parent = element.offsetParent;
  if (!(parent instanceof HTMLElement)) {
    const rect = element.getBoundingClientRect();
    return { x: rect.left, y: rect.top };
  }
  const base = parent.getBoundingClientRect();
  return { x: base.left + element.offsetLeft, y: base.top + element.offsetTop };
}

const STATE_COLORS: Record<string, string> = {
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

function FallbackPet({ state }: { state: string | null }) {
  const color = state === null ? "transparent" : (STATE_COLORS[state] ?? "#8ec5ff");
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
  frameProviderRef,
}: {
  state: string | null;
  avatar: LoadedAvatar | null;
  reaction: InteractionKind | null;
  hitTestRef?: RefObject<PetHitTest | null>;
  frameProviderRef?: RefObject<PetFrameProvider | null>;
}) {
  const selection =
    avatar === null ? null : selectAvatarAnimation(avatar.table, avatarStateKey(state));
  const animation: AnimationResource | null = selection?.animation ?? null;
  const frame = useAvatarFrame(animation);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const canvasWidth = animation?.canvas.width ?? 0;
  const canvasHeight = animation?.canvas.height ?? 0;
  const drawRect = useMemo(
    () => containRect({ width: canvasWidth, height: canvasHeight }, RENDER_SIZE, RENDER_SIZE),
    [canvasWidth, canvasHeight],
  );

  const provider = useCallback((): FrameViewport | null => {
    const canvas = canvasRef.current;
    if (canvas === null || animation === null) return null;
    return {
      mask: frame?.mask ?? null,
      drawRect: offsetRect(untransformedOrigin(canvas), drawRect),
      sourceWidth: animation.canvas.width,
      sourceHeight: animation.canvas.height,
    };
  }, [animation, drawRect, frame]);

  useEffect(() => {
    if (frameProviderRef === undefined) return;
    frameProviderRef.current = provider;
    return () => {
      frameProviderRef.current = null;
    };
  }, [frameProviderRef, provider]);

  useEffect(() => {
    if (hitTestRef === undefined) return;
    hitTestRef.current = (clientX, clientY) =>
      isAvatarPixel(provider(), { x: clientX, y: clientY });
    return () => {
      hitTestRef.current = null;
    };
  }, [hitTestRef, provider]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const context = canvas.getContext("2d");
    if (context === null) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (frame === null || frame.image === null) return;
    if (
      typeof ImageBitmap !== "undefined" &&
      frame.image instanceof ImageBitmap &&
      (frame.image.width === 0 || frame.image.height === 0)
    ) {
      return;
    }
    context.imageSmoothingEnabled = true;
    context.drawImage(frame.image, drawRect.left, drawRect.top, drawRect.width, drawRect.height);
  }, [drawRect, frame]);

  const drawable = frame !== null && frame.image !== null;

  return (
    <div
      style={{
        width: RENDER_SIZE,
        height: RENDER_SIZE,
        flexShrink: 0,
        transform: reaction === null ? undefined : REACTIONS[reaction],
        transformOrigin: animation === null ? "50% 50%" : transformOrigin(drawRect),
        transition: "transform 140ms ease-out",
      }}
    >
      {drawable ? (
        <canvas
          ref={canvasRef}
          data-tauri-drag-region
          width={RENDER_SIZE}
          height={RENDER_SIZE}
          aria-hidden="true"
          style={{ display: "block", width: RENDER_SIZE, height: RENDER_SIZE }}
        />
      ) : (
        <FallbackPet state={state} />
      )}
    </div>
  );
}
