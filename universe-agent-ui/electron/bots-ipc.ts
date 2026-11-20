/**
 * The IPC surface of the framed universe bots app, ported onto the scoped
 * IpcMain of its WebContentsView. Registering here instead of on the global
 * ipcMain means none of these ~268 channel names can collide with Universe's
 * own channels.
 *
 * Core desktop capabilities get real main-process implementations. The rest
 * are answered with honest empty defaults so the shipped UI renders instead
 * of throwing — a missing handler is never used to pretend a capability
 * exists.
 */
import {
  BrowserWindow,
  clipboard,
  desktopCapturer,
  dialog,
  Notification,
  shell,
  type IpcMain,
} from "electron";
import fs from "node:fs";
import os from "node:os";
import channels from "./bots-channels.json";
import { cuaConnection, cuaPermissions } from "./bots-cua";

const ARRAYISH = /(state|states|list|lists|routes|teams|servers|places|choices|sources|windows|sheets|notices|entries|items)$/;
let cuaReady = () => false;

function capabilities() {
  return {
    host: {
      platform: process.platform,
      homeDir: os.homedir(),
      label: "Universe desktop",
      session: "unknown",
      packaged: false,
    },
    windowChrome: "win-caption",
    screenPreview: { available: false, interaction: "none", reasonCode: "universe-host" },
    dictation: { available: false, engine: "none", onDevice: false, reasonCode: "universe-host" },
    localComputer: {
      available: cuaReady(),
      support: "supported",
      enabled: cuaReady(),
      status: cuaReady() ? "ready" : "unavailable",
      reasonCode: cuaReady() ? undefined : "universe-host",
    },
  };
}

function defaultFor(channel: string): unknown {
  if (channel === "desktop:capabilities") return capabilities();
  // Match the values the previously working iframe host answered: any other
  // locale string makes the app's i18n return undefined and its rail crashes.
  if (channel === "desktop-language:read") return false;
  if (channel === "desktop-language:changed") return false;
  if (channel === "desktop:skin") return true;
  if (channel === "desktop-analytics:read") return false;
  if (channel === "update:offered" || channel === "home:page" || channel === "home:updating")
    return false;
  if (channel === "screen:preview-intent") return false;
  if (channel === "window:state") return { maximized: false, minimized: false, fullScreen: false };
  if (channel === "desktop-notices:state") return { seen: [] };
  if (channel === "companion:state") return { devices: [], enabled: false, error: null };
  if (channel === "window-frame:state") return { frame: true };
  if (channel === "open-at-login:state") return false;
  if (channel === "updates-consent:state") return { choice: null };
  if (channel === "talk-shortcut:state") return { shortcut: null };
  if (channel === "workspaces:places")
    return { places: [], add: { cloud: false, link: false, address: false } };
  if (channel === "workspaces:state")
    return { name: "Universe Bots", local: true, localPath: os.homedir(), color: null, role: null, home: false, profile: null, profileName: null };
  if (channel === "workspaces:line") return { kind: "clean" };
  if (channel === "rail:offer" || channel === "rail:state") return null;
  if (channel === "perm:status") return { mic: false, pageMic: false };
  if (channel === "cua:connection") return cuaConnection() ?? { mode: "unavailable" };
  if (channel === "cua:permissions") return { available: false };
  if (channel === "cua:linux-status") return { available: false, enabled: false };
  if (channel.endsWith("-changed") || channel.endsWith(":changed")) return null;
  if (ARRAYISH.test(channel)) return [];
  if (channel.includes(":pick") || channel.includes("save-file")) return null;
  return null;
}

export function installBotsIpc(
  ipc: IpcMain,
  mainWindow: () => BrowserWindow | null,
  computerReady: () => boolean = () => false,
): number {
  cuaReady = computerReady;
  let registered = 0;
  const handled = new Set<string>();

  // Every channel gets an `on` listener first. This covers the renderer's
  // synchronous sends (which hang the page when nobody answers) and the
  // fire-and-forget sends.
  for (const channel of channels) {
    ipc.on(channel, event => {
      event.returnValue = defaultFor(channel);
    });
    registered += 1;
  }

  const handle = (channel: string, fn: (args: unknown[]) => unknown | Promise<unknown>) => {
    handled.add(channel);
    ipc.handle(channel, (_event, ...args) => fn(args));
  };

  // --- real main-process capabilities ---------------------------------------
  handle("desktop:capabilities", () => capabilities());

  // The computer panel polls this for a live frame of this computer. Return
  // a data URL the renderer can put straight into an <img>.
  handle("screen:frame", async () => {
    try {
      const sources = await desktopCapturer.getSources({
        types: ["screen"],
        thumbnailSize: { width: 1280, height: 720 },
      });
      const source = sources[0];
      return source ? source.thumbnail.toDataURL() : null;
    } catch (error) {
      console.log(`[bots] screen frame failed: ${String(error)}`);
      return null;
    }
  });

  handle("clipboard:write-text", args => {
    if (typeof args[0] !== "string") return false;
    clipboard.writeText(args[0]);
    return true;
  });

  const pickDirectory = async () => {
    const win = mainWindow();
    const res = await dialog.showOpenDialog(win!, {
      title: "Choose a folder",
      properties: ["openDirectory"],
    });
    return res.canceled || !res.filePaths.length ? null : res.filePaths[0];
  };
  handle("desktop:pick-folder", () => pickDirectory());
  handle("environments:pick-dir", () => pickDirectory());
  handle("sharing:folder", () => pickDirectory());
  handle("desktop:save-file", async () => {
    const win = mainWindow();
    const res = await dialog.showSaveDialog(win!);
    return res.canceled || !res.filePath ? null : res.filePath;
  });

  handle("desktop:open-external", args => {
    const url = String(args[0] ?? "");
    if (!/^https?:\/\//i.test(url)) return false;
    void shell.openExternal(url);
    return true;
  });
  handle("desktop:reveal-file", args => {
    const target = String(args[0] ?? "");
    if (!target || !fs.existsSync(target)) return false;
    shell.showItemInFolder(target);
    return true;
  });
  handle("desktop:open-folder", args => {
    const target = String(args[0] ?? "");
    if (!target || !fs.existsSync(target)) return false;
    void shell.openPath(target);
    return true;
  });
  handle("desktop:open-html", args => {
    const source = String(args[0] ?? "");
    if (!source || !fs.existsSync(source)) return false;
    void shell.openPath(source);
    return true;
  });

  handle("dialog:confirm", async args => {
    const win = mainWindow();
    const res = await dialog.showMessageBox(win!, {
      type: "question",
      buttons: ["Cancel", "OK"],
      title: "Universe bots",
      message: String(args[0] ?? "Are you sure?"),
    });
    return res.response === 1;
  });

  handle("desktop-notifications:show", args => {
    const note = (args[0] ?? {}) as { title?: string; body?: string };
    try {
      new Notification({ title: note.title || "Universe bots", body: note.body || "" }).show();
      return true;
    } catch {
      return false;
    }
  });

  const winAction = (action: string) => {
    const win = mainWindow();
    if (!win) return false;
    if (action === "minimize") win.minimize();
    else if (action === "close") win.close();
    else if (action === "toggle-maximize") {
      if (win.isMaximized()) win.unmaximize();
      else win.maximize();
    } else return false;
    return true;
  };
  for (const channel of ["window:minimize", "window:close", "window:toggle-maximize"]) {
    const action = channel.split(":")[1];
    handle(channel, () => winAction(action));
  }

  // Honest empty state for the workspaces / rail namespaces: the host owns
  // its own three-surface switcher, not the bots app's rail.
  const railState = { places: [], add: { cloud: false, link: false, address: false } };
  const ownPlace = { name: "Universe Bots", local: true, localPath: os.homedir(), color: null, role: null, home: false, profile: null, profileName: null };
  handle("workspaces:state", () => ownPlace);
  handle("workspaces:menu", () => []);
  handle("workspaces:places", () => railState);
  handle("workspaces:line", () => ({ kind: "clean" }));
  handle("workspaces:look", () => null);
  handle("rail:offer", () => null);
  // No updater exists in the embedded host: an honest null keeps the UI from
  // showing a fabricated "update failed" banner.
  handle("update:get-state", () => null);
  handle("cua:connection", () => cuaConnection() ?? { mode: "unavailable" });
  handle("cua:permissions", () => cuaPermissions());
  handle("updates-consent:state", () => ({ choice: null }));

  // The remaining channels are `invoke` calls with no host implementation
  // here. Answer them with honest empty values instead of letting Electron
  // reject the promise; `on` above already covers sync and fire-and-forget.
  for (const channel of channels) {
    if (!handled.has(channel)) {
      ipc.handle(channel, () => defaultFor(channel));
      handled.add(channel);
    }
  }

  return registered;
}
