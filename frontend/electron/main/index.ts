import { app, dialog, ipcMain } from "electron";
import path from "node:path";
import { IPC_BACKEND_INFO, type BackendInfo } from "../shared/ipc";
import { launchBackend } from "./backend/launcher";
import { BackendSupervisor } from "./backend/supervisor";
import { createPetWindow } from "./windows/pet-window";

const BACKEND_DIR = path.resolve(app.getAppPath(), "..", "backend");

function pythonPath(): string {
  if (process.env["PET_PYTHON"]) return process.env["PET_PYTHON"];
  const venv = path.join(BACKEND_DIR, ".venv");
  return process.platform === "win32"
    ? path.join(venv, "Scripts", "python.exe")
    : path.join(venv, "bin", "python");
}

let supervisor: BackendSupervisor | null = null;
let backendInfo: BackendInfo | null = null;
let quitting = false;

async function startBackend(): Promise<void> {
  const backend = await launchBackend({
    command: pythonPath(),
    args: ["main.py"],
    cwd: BACKEND_DIR,
    onLog: (line) => console.log(line),
  });
  backendInfo = { port: backend.port, token: backend.token };
  supervisor = new BackendSupervisor(backend.child, (info) => {
    console.error(`后端意外退出：代码 ${info.code}，信号 ${info.signal}`);
    dialog.showErrorBox("后端已退出", `后端意外退出（代码 ${info.code}），应用将关闭。`);
    app.quit();
  });
}

app.whenReady().then(async () => {
  ipcMain.handle(IPC_BACKEND_INFO, (): BackendInfo => {
    if (!backendInfo) throw new Error("后端尚未就绪");
    return backendInfo;
  });
  try {
    await startBackend();
  } catch (error) {
    dialog.showErrorBox("后端启动失败", String(error));
    app.quit();
    return;
  }
  createPetWindow();
});

app.on("window-all-closed", () => app.quit());

app.on("before-quit", (event) => {
  if (quitting || !supervisor) return;
  event.preventDefault();
  quitting = true;
  supervisor.stop().finally(() => app.quit());
});
