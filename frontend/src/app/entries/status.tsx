import { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { StatusWindow } from "../../modules/status/StatusWindow";
import { startStatusEvents } from "../../service/events";

function StatusApp() {
  useEffect(() => {
    let stop: (() => void) | null = null;
    let cancelled = false;
    startStatusEvents()
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

  return <StatusWindow />;
}

createRoot(document.getElementById("root") as HTMLElement).render(<StatusApp />);
