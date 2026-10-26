import type { PetState } from "../../api/types";

const STATE_COLORS: Record<PetState, string> = {
  idle: "#8ec5ff",
  thinking: "#c3a6ff",
  speaking: "#ffd166",
  executing: "#7bd389",
  sleeping: "#9aa5b1",
};

export function PetView({ state }: { state: PetState | null }) {
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
