import { createRoot } from "react-dom/client";

function ChatPlaceholder() {
  return (
    <main style={{ padding: 16, fontFamily: "system-ui, sans-serif", color: "#222" }}>
      对话窗口将在 T-041 实现。
    </main>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<ChatPlaceholder />);
