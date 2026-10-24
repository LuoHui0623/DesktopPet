import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiError } from "../../api/client";
import { checkBackendHealth } from "../../service/backend.service";

function describeError(error: unknown): string {
  if (error instanceof ApiError) return `${error.code}：${error.message}`;
  return String(error);
}

function PetApp() {
  const [status, setStatus] = useState("正在检查后端…");

  useEffect(() => {
    checkBackendHealth()
      .then((health) => setStatus(`后端正常（端口 ${health.port}，状态 ${health.status}）`))
      .catch((error: unknown) => {
        console.error("backend health check failed", error);
        setStatus(`后端错误：${describeError(error)}`);
      });
  }, []);

  return (
    <main style={{ padding: 16, fontFamily: "system-ui, sans-serif", color: "#222" }}>
      {status}
    </main>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<PetApp />);
