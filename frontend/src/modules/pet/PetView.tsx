import type { InteractionKind, PetState } from "../../api/types";
import type { LoadedAvatar } from "../../service/avatar.service";
import { selectAvatarAnimation } from "./avatar-view";
import { useAvatarFrame } from "./use-avatar-frame";

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
}: {
  state: PetState | null;
  avatar: LoadedAvatar | null;
  reaction: InteractionKind | null;
}) {
  const animation = avatar === null ? null : selectAvatarAnimation(avatar.pack, state);
  const frame = useAvatarFrame(animation);
  const src =
    animation === null || frame === undefined || avatar === null
      ? undefined
      : avatar.frameUrls.get(frame);

  return (
    <div
      style={{
        width: 120,
        height: 120,
        transform: reaction === null ? undefined : REACTIONS[reaction],
        transformOrigin: "50% 90%",
        transition: "transform 140ms ease-out",
      }}
    >
      {src === undefined ? (
        <FallbackPet state={state} />
      ) : (
        <img
          data-tauri-drag-region
          src={src}
          alt=""
          aria-hidden="true"
          draggable={false}
          width={120}
          height={120}
          style={{ width: 120, height: 120, objectFit: "contain" }}
        />
      )}
    </div>
  );
}
