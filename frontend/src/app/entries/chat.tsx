import { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { ChatWindow } from "../../modules/chat/ChatWindow";
import { startChatEvents } from "../../service/events";

function ChatApp() {
  useEffect(() => {
    let stop: (() => void) | null = null;
    let cancelled = false;
    startChatEvents()
      .then((close) => {
        if (cancelled) close();
        else stop = close;
      })
      .catch((error: unknown) => {
        console.error("连接后端事件失败", error);
      });
    return () => {
      cancelled = true;
      stop?.();
    };
  }, []);

  return <ChatWindow />;
}

createRoot(document.getElementById("root") as HTMLElement).render(<ChatApp />);
