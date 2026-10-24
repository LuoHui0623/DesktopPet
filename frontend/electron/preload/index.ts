import { contextBridge, ipcRenderer } from "electron";
import { IPC_BACKEND_INFO, type BackendInfo } from "../shared/ipc";

contextBridge.exposeInMainWorld("petBackend", {
  getInfo: (): Promise<BackendInfo> => ipcRenderer.invoke(IPC_BACKEND_INFO),
});
