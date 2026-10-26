import { useStore } from "zustand";
import { openChatWindow } from "../../service/window.service";
import { petStore } from "../../service/stores/pet.store";
import { PetView } from "./PetView";
import { petStatusLabel } from "./status";

export function PetWindow() {
  const connected = useStore(petStore, (state) => state.connected);
  const status = useStore(petStore, (state) => state.status);
  const label = petStatusLabel(connected, status);

  return (
    <main
      data-tauri-drag-region
      role="button"
      tabIndex={0}
      aria-label={`宠物状态：${label}，点击打开对话`}
      onClick={() => {
        openChatWindow().catch((error: unknown) => {
          console.error("打开对话窗口失败", error);
        });
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openChatWindow().catch((error: unknown) => {
            console.error("打开对话窗口失败", error);
          });
        }
      }}
      style={{
        width: 320,
        height: 240,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        background: "transparent",
        color: "#222",
        fontFamily: "system-ui, sans-serif",
        userSelect: "none",
        cursor: "pointer",
      }}
    >
      <PetView state={connected ? status : null} />
      <span
        data-tauri-drag-region
        style={{
          padding: "2px 10px",
          borderRadius: 10,
          background: connected ? "rgba(255,255,255,0.85)" : "rgba(120,120,120,0.85)",
          color: connected ? "#222" : "#fff",
          fontSize: 13,
        }}
      >
        {label}
      </span>
    </main>
  );
}
