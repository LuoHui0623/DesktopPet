import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type { BackendInfo } from "../../../electron/shared/ipc";

function PetApp() {
  const [status, setStatus] = useState("正在连接后端…");

  useEffect(() => {
    window.petBackend
      .getInfo()
      .then((info: BackendInfo) => setStatus(`后端已连接，端口 ${info.port}`))
      .catch((error: unknown) => setStatus(`连接失败：${String(error)}`));
  }, []);

  return (
    <main style={{ padding: 16, fontFamily: "system-ui, sans-serif", color: "#222" }}>
      {status}
    </main>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<PetApp />);
