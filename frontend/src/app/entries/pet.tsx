import { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { PetWindow } from "../../modules/pet/PetWindow";
import { startPetEvents } from "../../service/events";
import { startPerception } from "../../service/perception.service";

function PetApp() {
  useEffect(() => {
    let stopEvents: (() => void) | null = null;
    let stopPerception: (() => void) | null = null;
    let cancelled = false;
    startPetEvents()
      .then((close) => {
        if (cancelled) close();
        else stopEvents = close;
      })
      .catch((error: unknown) => {
        console.error("连接后端事件失败", error);
      });
    startPerception()
      .then((close) => {
        if (cancelled) close();
        else stopPerception = close;
      })
      .catch((error: unknown) => {
        console.error("启动感知采集失败", error);
      });
    return () => {
      cancelled = true;
      stopEvents?.();
      stopPerception?.();
    };
  }, []);

  return <PetWindow />;
}

createRoot(document.getElementById("root") as HTMLElement).render(<PetApp />);
