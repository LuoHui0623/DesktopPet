import { ModelSettings } from "./ModelSettings";

export function SettingsWindow() {
  return (
    <div
      style={{
        height: "100vh",
        boxSizing: "border-box",
        overflowY: "auto",
        padding: 16,
        fontFamily: "system-ui, sans-serif",
        color: "#222",
        background: "#fff",
      }}
    >
      <h1 style={{ fontSize: 18, margin: "0 0 16px" }}>设置</h1>
      <ModelSettings />
    </div>
  );
}
