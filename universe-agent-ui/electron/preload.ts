/**
 * electron/preload.ts — the ONLY bridge between the sandboxed renderer and
 * the main process. A small, typed surface:
 *
 *   universe.openPath(target)   — open an artifact's folder in the OS explorer
 *   universe.win(action)        — minimize | maximize | close
 *   universe.bridgeInfo()       — port/url/status for the HTTP client
 *   universe.bridgeRestart()    — manual retry after a failed bridge
 *   universe.pickFolder()       — native choose-folder dialog (Settings →
 *                             General work folder); null on cancel
 *   universe.onBridgeStatus(cb) — live backend status (starting/up/down/…)
 */
import { contextBridge, ipcRenderer, webUtils } from "electron";

type BridgeStatus = "starting" | "up" | "restarting" | "down" | "failed";

const api = {
  openPath: (target: string) =>
    ipcRenderer.invoke("universe:open-path", target) as Promise<boolean>,
  win: (action: "minimize" | "maximize" | "close") =>
    ipcRenderer.invoke("universe:win", action) as Promise<boolean>,
  /** Copy the message chips: the main process writes the OS clipboard
   *  (navigator.clipboard rejects in some webviews — user bug 2026-10-30) */
  copyText: (text: string) =>
    ipcRenderer.invoke("universe:copy-text", text) as Promise<boolean>,
  /** The Bots surface is a native view in main; these two calls keep its
   *  bounds in sync with the DOM host and show/hide it on route changes. */
  botsViewVisible: (visible: boolean) =>
    ipcRenderer.send("universe:bots-view-visible", visible),
  botsViewBounds: (bounds: {
    x: number;
    y: number;
    width: number;
    height: number;
  }) => ipcRenderer.send("universe:bots-view-bounds", bounds),
  onNavigateSurface: (cb: (surface: string) => void) => {
    const listener = (_e: unknown, surface: string) => cb(surface);
    ipcRenderer.on("universe:navigate-surface", listener);
    return () => ipcRenderer.removeListener("universe:navigate-surface", listener);
  },
  onCreateBotViaChat: (cb: () => void) => {
    const listener = () => cb();
    ipcRenderer.on("universe:create-bot-via-chat", listener);
    return () => ipcRenderer.removeListener("universe:create-bot-via-chat", listener);
  },
  bridgeInfo: () =>
    ipcRenderer.invoke("universe:bridge-info") as Promise<{
      port: string;
      url: string;
      status: BridgeStatus;
      token: string;
    }>,
  bridgeRestart: () =>
    ipcRenderer.invoke("universe:bridge-restart") as Promise<boolean>,
  pickFolder: () =>
    ipcRenderer.invoke("universe:pick-folder") as Promise<string | null>,
  pickFile: () =>
    ipcRenderer.invoke("universe:pick-file") as Promise<string | null>,
  /** pasted-image media (renderer paste of a screenshot bitmap): the main
   *  process reads the clipboard, writes ONE PNG into the OS temp dir and
   *  returns its absolute path (null when the clipboard holds no image). */
  pasteClipboardMedia: () =>
    ipcRenderer.invoke(
      "universe:paste-clipboard-media"
    ) as Promise<string | null>,
  /** absolute path of a dropped/pasted OS file (image/video/docs).
   *  Electron 43 removed File.path — webUtils.getPathForFile is the
   *  official replacement, and it must be called IN the preload with the
   *  File bridged over from the renderer main world. */
  pathForFile: (file: File) => webUtils.getPathForFile(file),
  onBridgeStatus: (
    cb: (s: { status: BridgeStatus; detail: string; port: string }) => void
  ) => {
    const listener = (_e: unknown, payload: Parameters<typeof cb>[0]) =>
      cb(payload);
    ipcRenderer.on("universe:bridge-status", listener);
    return () => ipcRenderer.removeListener("universe:bridge-status", listener);
  },
};

contextBridge.exposeInMainWorld("universe", api);

export type UniverseDesktopApi = typeof api;
