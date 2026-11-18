// Tiny sandboxed preload for the transparent surface-switcher view that
// floats over the bots renderer. It exposes exactly one capability.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("botsSwitcher", {
  switchTo: (surface) => ipcRenderer.send("universe:bots-switch", surface),
});
