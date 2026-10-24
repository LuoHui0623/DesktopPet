import type { BackendInfo } from "../../electron/shared/ipc";

declare global {
  interface Window {
    petBackend: {
      getInfo: () => Promise<BackendInfo>;
    };
  }
}

export {};
