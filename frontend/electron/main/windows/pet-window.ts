import { BrowserWindow } from "electron";
import path from "node:path";

export function createPetWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 320,
    height: 240,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const devServer = process.env["ELECTRON_RENDERER_URL"];
  if (devServer) {
    void window.loadURL(`${devServer}/app/entries/pet.html`);
  } else {
    void window.loadFile(path.join(__dirname, "../renderer/app/entries/pet.html"));
  }
  return window;
}
