import { useEffect } from "react";
import { createRoot } from "react-dom/client";
import { PetWindow } from "../../modules/pet/PetWindow";
import { startPetEvents } from "../../service/events";

function PetApp() {
  useEffect(() => {
    let stop: (() => void) | null = null;
    let cancelled = false;
    startPetEvents()
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

  return <PetWindow />;
}

createRoot(document.getElementById("root") as HTMLElement).render(<PetApp />);
