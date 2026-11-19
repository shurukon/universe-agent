import { createRequire as __cr } from 'node:module'; import { fileURLToPath as __f2p } from 'node:url'; import * as __p from 'node:path'; const require = __cr(import.meta.url); const __dirname = __p.dirname(__f2p(import.meta.url));
var __require = /* @__PURE__ */ ((x) => typeof require !== "undefined" ? require : typeof Proxy !== "undefined" ? new Proxy(x, {
  get: (a, b) => (typeof require !== "undefined" ? require : a)[b]
}) : x)(function(x) {
  if (typeof require !== "undefined") return require.apply(this, arguments);
  throw Error('Dynamic require of "' + x + '" is not supported');
});

// electron/main.ts
import {
  app,
  BrowserWindow as BrowserWindow2,
  clipboard as clipboard2,
  dialog as dialog2,
  ipcMain,
  net,
  protocol,
  screen,
  session,
  shell as shell2,
  utilityProcess,
  WebContentsView
} from "electron";
import { spawn, spawnSync } from "node:child_process";
import http from "node:http";
import os2 from "node:os";
import path2 from "node:path";
import fs2 from "node:fs";
import crypto from "node:crypto";

// electron/bots-ipc.ts
import {
  clipboard,
  desktopCapturer,
  dialog,
  Notification,
  shell
} from "electron";
import fs from "node:fs";
import os from "node:os";

// electron/bots-channels.json
var bots_channels_default = [
  "accounts:add-browser",
  "accounts:add-cancel",
  "accounts:add-reopen",
  "accounts:add-result",
  "accounts:add-start",
  "accounts:add-verify",
  "accounts:changed",
  "accounts:list",
  "accounts:offered",
  "accounts:open",
  "accounts:open-sheet",
  "accounts:sheet-closed",
  "accounts:sheet-pending",
  "accounts:sign-out",
  "android-device:frame",
  "android-device:input",
  "android-device:status",
  "app:open-settings",
  "approvals:set-trusted-mode",
  "call:active",
  "chat:open",
  "clipboard:write-text",
  "cloud-account:begin",
  "cloud-account:cancel",
  "cloud-account:cancelPlan",
  "cloud-account:checkout",
  "cloud-account:connectHome",
  "cloud-account:connectHomeForPhone",
  "cloud-account:copyCheckoutLink",
  "cloud-account:interest",
  "cloud-account:keepPlan",
  "cloud-account:look",
  "cloud-account:offer",
  "cloud-account:openDashboard",
  "cloud-account:openTeam",
  "cloud-account:readySeen",
  "cloud-account:refresh",
  "cloud-account:reopen",
  "cloud-account:reopenCheckout",
  "cloud-account:setLook",
  "cloud-account:sheet",
  "cloud-account:signInAgain",
  "cloud-account:signInCancel",
  "cloud-account:signInStart",
  "cloud-account:signInVerify",
  "cloud-account:signOut",
  "cloud-account:state",
  "cloud-account:state-changed",
  "cloud-add:copy-link",
  "cloud-add:interest",
  "cloud-add:open",
  "cloud-add:organization",
  "cloud-add:reopen",
  "cloud-add:sheet",
  "cloud-add:start",
  "cloud-add:state",
  "cloud-plan:cancel",
  "cloud-plan:keep",
  "cloud-plan:manage",
  "cloud-plan:notice-seen",
  "cloud-plan:restart-now",
  "cloud-plan:room-not-now",
  "cloud-plan:state",
  "cloud-plan:upgrade",
  "cloud-plan:upgrade-preview",
  "cloud-send:bot-choices",
  "cloud-send:bot-move-retry",
  "cloud-send:bot-move-state",
  "cloud-send:dismiss",
  "cloud-send:move-bots",
  "cloud-send:move-everything",
  "cloud-send:move-retry",
  "cloud-send:notice",
  "cloud-send:notice-changed",
  "cloud-send:open-my-cloud",
  "cloud-send:team-bring",
  "cloud-send:team-bring-move",
  "cloud-send:team-bring-status",
  "cloud-send:team-move",
  "cloud-send:team-move-coded",
  "cloud-send:team-open",
  "cloud-send:team-retry",
  "cloud-send:teams",
  "companion-account:prepare",
  "companion-account:request-code",
  "companion-account:retry",
  "companion-account:sign-out",
  "companion-account:state",
  "companion-account:verify-code",
  "companion:browser-control",
  "companion:cloud-desktop",
  "companion:keep-awake",
  "companion:notice-dismiss",
  "companion:pairing",
  "companion:pairing-renew",
  "companion:refresh-tailscale",
  "companion:revoke",
  "companion:route",
  "companion:routes",
  "companion:start",
  "companion:state",
  "companion:stop",
  "companion:tailscale",
  "companion:tailscale-computers",
  "company-backups:cancel",
  "company-backups:client-state",
  "company-backups:collect-client-state",
  "company-backups:configure-schedule",
  "company-backups:create",
  "company-backups:delete",
  "company-backups:list",
  "company-backups:preview",
  "company-backups:restore",
  "company-backups:state",
  "company-backups:state-changed",
  "credential-store:reset",
  "credential-store:unreadable",
  "credential:set",
  "cua:linux-disable",
  "cua:linux-enable",
  "cua:linux-retry",
  "cua:linux-status",
  "desktop-analytics:read",
  "desktop-analytics:set",
  "desktop-language:changed",
  "desktop-language:read",
  "desktop-language:set",
  "desktop-language:shown",
  "desktop-notices:seen",
  "desktop-notices:state",
  "desktop-notifications:open",
  "desktop-notifications:show",
  "desktop-profile:system-name",
  "desktop-remote:disconnect",
  "desktop-remote:pair",
  "desktop-remote:state",
  "desktop-viewer:close",
  "desktop-viewer:open",
  "desktop-viewer:state",
  "desktop-viewer:state-now",
  "desktop-workspace:close",
  "desktop-workspace:layout",
  "desktop-workspace:open",
  "desktop-workspace:set-interactive",
  "desktop-workspace:state",
  "desktop:capabilities",
  "desktop:capabilities-changed",
  "desktop:export-diagnostics",
  "desktop:open-external",
  "desktop:open-folder",
  "desktop:open-logs",
  "desktop:pick-folder",
  "desktop:relaunch",
  "desktop:reveal-file",
  "desktop:skin",
  "desktop:unread-count",
  "dialog:confirm",
  "engine:open-terminal",
  "environments:add-from-link",
  "environments:create",
  "environments:forget",
  "environments:pick-dir",
  "environments:retry",
  "environments:state",
  "environments:status",
  "environments:status-changed",
  "environments:switch",
  "files:check",
  "files:deletes",
  "files:locate",
  "files:more-space",
  "files:open",
  "files:outputs",
  "files:pick",
  "files:share",
  "files:state",
  "files:state-changed",
  "files:stop",
  "home:page",
  "home:remove",
  "home:repair",
  "home:retry",
  "home:sign-in",
  "home:state",
  "home:state-changed",
  "home:updating",
  "one-home:keep",
  "one-home:keep-set",
  "open-at-login:set",
  "open-at-login:state",
  "organization:begin",
  "organization:cancel",
  "organization:disconnect",
  "organization:refresh",
  "organization:reopen",
  "organization:settings-opened",
  "organization:state",
  "organization:state-changed",
  "package:install",
  "perm:open-settings",
  "perm:request-mic",
  "perm:status",
  "rail:beside",
  "rail:draws-frame",
  "rail:expand",
  "rail:framed",
  "rail:hide",
  "rail:offer",
  "rail:open-computer",
  "rail:skin",
  "rail:state",
  "routines:keep-awake",
  "routines:wake-state",
  "screen:frame",
  "screen:preview-intent",
  "sharing:activity",
  "sharing:allow",
  "sharing:folder",
  "sharing:pause",
  "sharing:resume",
  "sharing:revoke",
  "sharing:save",
  "sharing:servers",
  "sharing:state",
  "speech:end",
  "speech:finish",
  "speech:start",
  "speech:stop",
  "speech:transcript",
  "talk-shortcut:set",
  "talk-shortcut:state",
  "two-places:accept",
  "two-places:changed",
  "two-places:dismiss",
  "two-places:state",
  "update:check",
  "update:for-server-page",
  "update:install",
  "update:offered",
  "update:seen",
  "update:state",
  "updates-consent:choose",
  "updates-consent:state",
  "window-frame:set",
  "window-frame:state",
  "window:close",
  "window:maximized-changed",
  "window:minimize",
  "window:state",
  "window:toggle-maximize",
  "workspaces:add",
  "workspaces:changed",
  "workspaces:color",
  "workspaces:line",
  "workspaces:local-edit",
  "workspaces:local-new",
  "workspaces:local-remove",
  "workspaces:look",
  "workspaces:make-home",
  "workspaces:menu",
  "workspaces:new-local",
  "workspaces:open",
  "workspaces:open-settings",
  "workspaces:places",
  "workspaces:probe",
  "workspaces:recolor",
  "workspaces:remove",
  "workspaces:state"
];

// electron/bots-cua.ts
import path from "node:path";
import { pathToFileURL } from "node:url";
var cua = null;
var originSetter = null;
function cuaConnection() {
  return connection;
}
var connection = null;
async function startBotsCua(repoRoot, origin, onConnection) {
  try {
    if (!process.env.CUA_DRIVER_PATH) {
      const staged = path.join(
        repoRoot,
        "universe-bots",
        "dist-native",
        "cua-win32-x64",
        process.platform === "win32" ? "cua-driver.exe" : "cua-driver"
      );
      if (__require("node:fs").existsSync(staged)) process.env.CUA_DRIVER_PATH = staged;
    }
    const localOrigin = __require(path.join(repoRoot, "universe-bots", "electron", "local-origin.cjs"));
    localOrigin.setLocalOrigin(origin);
    originSetter = localOrigin.setLocalOrigin;
    const moduleUrl = pathToFileURL(
      path.join(repoRoot, "universe-bots", "electron", "cua.mjs")
    ).href;
    const loaded = await import(moduleUrl);
    cua = loaded;
    loaded.setCuaStateListener((next) => {
      connection = next;
      onConnection?.(next);
    });
    loaded.registerCuaIpc();
    connection = await loaded.startCua();
    onConnection?.(connection);
    console.log(`[cua] ${connection?.mode ?? "unknown"}${connection?.reason ? ": " + connection.reason : ""}`);
    return loaded;
  } catch (error) {
    console.log(`[cua] start failed: ${String(error)}`);
    cua = null;
    return null;
  }
}
function grantBotsComputer(proc) {
  if (!cua || !proc) return;
  try {
    cua.grantLocalComputer(proc, true);
  } catch (error) {
    console.log(`[cua] grant failed: ${String(error)}`);
  }
}
async function cuaPermissions() {
  if (!cua) return { available: false };
  try {
    return await cua.cuaPermissionsStatus();
  } catch (error) {
    console.log(`[cua] permissions query failed: ${String(error)}`);
    return { available: false };
  }
}
function setBotsCuaOrigin(origin) {
  try {
    originSetter?.(origin);
  } catch {
  }
}
async function stopBotsCua() {
  const loaded = cua;
  cua = null;
  if (!loaded) return;
  try {
    await loaded.stopCua();
  } catch (error) {
    console.log(`[cua] stop failed: ${String(error)}`);
  }
}

// electron/bots-ipc.ts
var ARRAYISH = /(state|states|list|lists|routes|teams|servers|places|choices|sources|windows|sheets|notices|entries|items)$/;
var cuaReady = () => false;
function capabilities() {
  return {
    host: {
      platform: process.platform,
      homeDir: os.homedir(),
      label: "Universe desktop",
      session: "unknown",
      packaged: false
    },
    windowChrome: "win-caption",
    screenPreview: { available: false, interaction: "none", reasonCode: "universe-host" },
    dictation: { available: false, engine: "none", onDevice: false, reasonCode: "universe-host" },
    localComputer: {
      available: cuaReady(),
      support: "supported",
      enabled: cuaReady(),
      status: cuaReady() ? "ready" : "unavailable",
      reasonCode: cuaReady() ? void 0 : "universe-host"
    }
  };
}
function defaultFor(channel) {
  if (channel === "desktop:capabilities") return capabilities();
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
function installBotsIpc(ipc, mainWindow, computerReady = () => false) {
  cuaReady = computerReady;
  let registered = 0;
  const handled = /* @__PURE__ */ new Set();
  for (const channel of bots_channels_default) {
    ipc.on(channel, (event) => {
      event.returnValue = defaultFor(channel);
    });
    registered += 1;
  }
  const handle = (channel, fn) => {
    handled.add(channel);
    ipc.handle(channel, (_event, ...args) => fn(args));
  };
  handle("desktop:capabilities", () => capabilities());
  handle("screen:frame", async () => {
    try {
      const sources = await desktopCapturer.getSources({
        types: ["screen"],
        thumbnailSize: { width: 1280, height: 720 }
      });
      const source = sources[0];
      return source ? source.thumbnail.toDataURL() : null;
    } catch (error) {
      console.log(`[bots] screen frame failed: ${String(error)}`);
      return null;
    }
  });
  handle("clipboard:write-text", (args) => {
    if (typeof args[0] !== "string") return false;
    clipboard.writeText(args[0]);
    return true;
  });
  const pickDirectory = async () => {
    const win = mainWindow();
    const res = await dialog.showOpenDialog(win, {
      title: "Choose a folder",
      properties: ["openDirectory"]
    });
    return res.canceled || !res.filePaths.length ? null : res.filePaths[0];
  };
  handle("desktop:pick-folder", () => pickDirectory());
  handle("environments:pick-dir", () => pickDirectory());
  handle("sharing:folder", () => pickDirectory());
  handle("desktop:save-file", async () => {
    const win = mainWindow();
    const res = await dialog.showSaveDialog(win);
    return res.canceled || !res.filePath ? null : res.filePath;
  });
  handle("desktop:open-external", (args) => {
    const url = String(args[0] ?? "");
    if (!/^https?:\/\//i.test(url)) return false;
    void shell.openExternal(url);
    return true;
  });
  handle("desktop:reveal-file", (args) => {
    const target = String(args[0] ?? "");
    if (!target || !fs.existsSync(target)) return false;
    shell.showItemInFolder(target);
    return true;
  });
  handle("desktop:open-folder", (args) => {
    const target = String(args[0] ?? "");
    if (!target || !fs.existsSync(target)) return false;
    void shell.openPath(target);
    return true;
  });
  handle("desktop:open-html", (args) => {
    const source = String(args[0] ?? "");
    if (!source || !fs.existsSync(source)) return false;
    void shell.openPath(source);
    return true;
  });
  handle("dialog:confirm", async (args) => {
    const win = mainWindow();
    const res = await dialog.showMessageBox(win, {
      type: "question",
      buttons: ["Cancel", "OK"],
      title: "Universe bots",
      message: String(args[0] ?? "Are you sure?")
    });
    return res.response === 1;
  });
  handle("desktop-notifications:show", (args) => {
    const note = args[0] ?? {};
    try {
      new Notification({ title: note.title || "Universe bots", body: note.body || "" }).show();
      return true;
    } catch {
      return false;
    }
  });
  const winAction = (action) => {
    const win = mainWindow();
    if (!win) return false;
    if (action === "minimize") win.minimize();
    else if (action === "close") win.close();
    else if (action === "toggle-maximize")
      win.isMaximized() ? win.unmaximize() : win.maximize();
    else return false;
    return true;
  };
  for (const channel of ["window:minimize", "window:close", "window:toggle-maximize"]) {
    const action = channel.split(":")[1];
    handle(channel, () => winAction(action));
  }
  const railState = { places: [], add: { cloud: false, link: false, address: false } };
  const ownPlace = { name: "Universe Bots", local: true, localPath: os.homedir(), color: null, role: null, home: false, profile: null, profileName: null };
  handle("workspaces:state", () => ownPlace);
  handle("workspaces:menu", () => []);
  handle("workspaces:places", () => railState);
  handle("workspaces:line", () => ({ kind: "clean" }));
  handle("workspaces:look", () => null);
  handle("rail:offer", () => null);
  handle("update:get-state", () => null);
  handle("cua:connection", () => cuaConnection() ?? { mode: "unavailable" });
  handle("cua:permissions", () => cuaPermissions());
  handle("updates-consent:state", () => ({ choice: null }));
  for (const channel of bots_channels_default) {
    if (!handled.has(channel)) {
      ipc.handle(channel, () => defaultFor(channel));
      handled.add(channel);
    }
  }
  return registered;
}

// electron/main.ts
var BRIDGE_PORT = process.env.UNIVERSE_BRIDGE_PORT || process.env.GALAXY_BRIDGE_PORT || "8738";
var BRIDGE_URL = `http://127.0.0.1:${BRIDGE_PORT}`;
var WS_URL = `ws://127.0.0.1:${BRIDGE_PORT}`;
var DEV_URL = process.env.ELECTRON_START_URL || "http://127.0.0.1:5199";
var HEALTH_POLL_MS = 3e4;
var MAX_BRIDGE_ATTEMPTS = 5;
var MAX_BOTS_ATTEMPTS = 3;
var BRIDGE_TOKEN = crypto.randomBytes(32).toString("base64url");
function resolveRepoRoot() {
  const candidates = [
    process.env.UNIVERSE_ROOT || process.env.GALAXY_ROOT || "",
    path2.resolve(__dirname, "..", ".."),
    path2.resolve(__dirname, "..", "..", "..")
  ].filter(Boolean);
  for (const dir of candidates) {
    if (fs2.existsSync(path2.join(dir, "universe_desktop_bridge"))) return dir;
  }
  return candidates[candidates.length - 1] || process.cwd();
}
protocol.registerSchemesAsPrivileged([
  {
    scheme: "app",
    privileges: {
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      standard: true
    }
  }
]);
if (!process.env.UNIVERSE_GPU && !process.env.GALAXY_GPU) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu-compositing");
  app.commandLine.appendSwitch("enable-unsafe-swiftshader");
}
process.on("uncaughtException", (err) => {
  if (err?.code === "EPIPE") return;
  try {
    console.error("[fatal] uncaught:", err);
  } catch {
  }
  process.exitCode = 1;
});
var gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  bootstrapDesktop();
  app.on("second-instance", (_evt, argv) => {
    const m = argv.map((a) => a.match(/(?:^|[?&-])goal=([A-Za-z0-9_-]+)/)).find(Boolean);
    console.log(`[deep-link] second-instance argv=${JSON.stringify(argv)} goal=${m ? m[1] : null}`);
    const w = BrowserWindow2.getAllWindows()[0];
    if (!w) return;
    if (w.isMinimized()) w.restore();
    w.show();
    w.focus();
    if (m) {
      const url = app.isPackaged ? `app://universe/?goal=${m[1]}` : `${DEV_URL}/?goal=${m[1]}`;
      void w.loadURL(url);
    }
  });
}
function bootstrapDesktop() {
  let appQuitting = false;
  let bridgeProc = null;
  let bridgeAttempts = 0;
  let bridgeStatus = "starting";
  let healthTimer = null;
  function pushStatus(status, detail = "") {
    bridgeStatus = status;
    for (const w of BrowserWindow2.getAllWindows()) {
      w.webContents.send("universe:bridge-status", {
        status,
        detail,
        port: BRIDGE_PORT
      });
    }
  }
  function startBridge() {
    if (app.isPackaged && !fs2.existsSync(path2.join(resolveRepoRoot(), "universe_desktop_bridge"))) {
      pushStatus("failed", "Universe backend not found next to the app");
      return;
    }
    const python = process.env.UNIVERSE_PYTHON || process.env.GALAXY_PYTHON || "python";
    bridgeAttempts += 1;
    pushStatus(bridgeAttempts > 1 ? "restarting" : "starting");
    try {
      bridgeProc = spawn(python, ["-m", "universe_desktop_bridge"], {
        cwd: resolveRepoRoot(),
        env: {
          ...process.env,
          UNIVERSE_BRIDGE_PORT: BRIDGE_PORT,
          UNIVERSE_BRIDGE_TOKEN: BRIDGE_TOKEN,
          // the bots surface asks the bridge where universe bots lives
          UNIVERSE_BOTS_ORIGIN: BOTS_ORIGIN,
          UNIVERSE_BOTS_DATA_DIR: path2.join(os2.homedir(), ".universe", "bots"),
          // the bridge is this app's own process, so it carries the same
          // capability the harness gives the desktop: the routes that change
          // something refuse a request without it.
          UNIVERSE_BOTS_OWNER_TOKEN: BOTS_OWNER_TOKEN,
          PYTHONUNBUFFERED: "1"
        },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true
      });
    } catch (err) {
      pushStatus("failed", `spawn failed: ${String(err)}`);
      return;
    }
    bridgeProc.stdout?.on("data", (d) => {
      const line = d.toString().trim();
      if (line) console.log(`[bridge] ${line}`);
      if (line.includes("Uvicorn running")) pushStatus("up");
    });
    bridgeProc.stderr?.on("data", (d) => {
      const line = d.toString().trim();
      if (line) console.log(`[bridge:err] ${line}`);
    });
    bridgeProc.on("error", (err) => {
      bridgeProc = null;
      if (appQuitting) return;
      pushStatus("failed", `bridge spawn error: ${err.message}`);
    });
    bridgeProc.on("exit", (code) => {
      bridgeProc = null;
      if (appQuitting) return;
      if (bridgeAttempts >= MAX_BRIDGE_ATTEMPTS) {
        pushStatus(
          "failed",
          `bridge exited (code ${code}) after ${bridgeAttempts} attempts`
        );
        return;
      }
      const delay = Math.min(500 * 2 ** (bridgeAttempts - 1), 1e4);
      console.log(
        `[bridge] exited code=${code}; restart #${bridgeAttempts} in ${delay}ms`
      );
      pushStatus("restarting", `restart in ${delay}ms`);
      setTimeout(() => {
        if (!appQuitting) startBridge();
      }, delay);
    });
  }
  function stopBridge() {
    const proc = bridgeProc;
    bridgeProc = null;
    if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;
    try {
      if (process.platform === "win32" && proc.pid) {
        spawnSync("taskkill", ["/pid", String(proc.pid), "/T", "/F"], {
          stdio: "ignore",
          windowsHide: true
        });
      } else {
        proc.kill("SIGTERM");
        setTimeout(() => {
          try {
            if (proc.exitCode === null && proc.signalCode === null)
              proc.kill("SIGKILL");
          } catch {
          }
        }, 3e3).unref?.();
      }
    } catch {
      try {
        proc.kill();
      } catch {
      }
    }
  }
  let BOTS_PORT = process.env.UNIVERSE_BOTS_PORT || "8799";
  let BOTS_ORIGIN = `http://127.0.0.1:${BOTS_PORT}`;
  const BOTS_OWNER_HEADER = "X-UB-Desktop-Owner";
  const BOTS_OWNER_TOKEN = crypto.randomBytes(32).toString("base64url");
  let botsProc = null;
  let botsAttempts = 0;
  function botsFrameAncestors() {
    const dev = "http://127.0.0.1:5199";
    const packaged = "app://universe";
    return process.env.UNIVERSE_BOTS_FRAME_ANCESTORS || (app.isPackaged ? packaged : dev);
  }
  function installBotsOwnerHeader(target) {
    target.webRequest.onBeforeSendHeaders((details, callback) => {
      const headers = { ...details.requestHeaders };
      try {
        const target2 = new URL(details.url);
        if (target2.hostname === "127.0.0.1" && Number(target2.port || 80) === Number(BOTS_PORT)) {
          headers[BOTS_OWNER_HEADER] = BOTS_OWNER_TOKEN;
        }
      } catch {
      }
      callback({ requestHeaders: headers });
    });
  }
  const UNIVERSE_BOTS_FIRST_RUN_HINTS = [
    // the guided tour's steps
    "tour.composer",
    "tour.model",
    "tour.computer",
    "tour.computer-browser",
    "tour.tools",
    "tour.apps",
    "tour.apps-panel",
    "tour.automations",
    "tour.automations-page",
    "tour.done",
    // the launch notices
    "grok-move-2026-11",
    "phone-app-update",
    "simple-mode-prompt",
    "whats-new-2026-11",
    "updates-consent",
    "two-places",
    "product-hunt-launch-2026-10",
    "cloud-trial-intro-v1",
    "cloud-trial-notice",
    "cloud-notice",
    "modelgo-offer-2026-11",
    "boat-credit-2026-11",
    "announcement",
    "github-star-dismissed",
    // the cloud and the first chat
    "onboarding-cloud-v1",
    "cloud-setup-hidden",
    "cloud-welcome-shown",
    "cloud-welcome-bring",
    "cloud-welcome-engine",
    "cloud-welcome-files",
    "cloud-welcome-done",
    "met-chief-v1",
    "pro-introduction-dismissed-v3"
  ];
  function markUniverseBotsWelcomed(dataDir) {
    const file = path2.join(dataDir, "config.json");
    let config = {};
    try {
      const parsed = JSON.parse(fs2.readFileSync(file, "utf8"));
      if (parsed && typeof parsed === "object") {
        config = parsed;
      }
    } catch {
    }
    const record = config.onboarding && typeof config.onboarding === "object" ? config.onboarding : {};
    const hints = Array.isArray(record.hintsSeen) ? record.hintsSeen.filter((hint) => typeof hint === "string") : [];
    const merged = [.../* @__PURE__ */ new Set([...hints, ...UNIVERSE_BOTS_FIRST_RUN_HINTS])];
    if (record.completedAt && typeof record.version === "number" && record.version >= 1) {
      if (merged.length === hints.length) return;
    }
    config.onboarding = {
      ...record,
      completedAt: typeof record.completedAt === "string" && record.completedAt ? record.completedAt : (/* @__PURE__ */ new Date()).toISOString(),
      version: 1,
      hintsSeen: merged
    };
    fs2.writeFileSync(file, `${JSON.stringify(config, null, 2)}
`, "utf8");
  }
  async function welcomeUniverseBots() {
    const headers = { [BOTS_OWNER_HEADER]: BOTS_OWNER_TOKEN };
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        const response = await fetch(`${BOTS_ORIGIN}/api/bots`, { headers });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        if (botsView && !botsView.webContents.isDestroyed() && !botsViewLoaded) {
          botsViewLoaded = true;
          void botsView.webContents.loadURL(BOTS_ORIGIN).catch(() => {
            botsViewLoaded = false;
          });
        }
        const body = await response.json();
        const bots = Array.isArray(body.bots) ? body.bots : [];
        const chief = bots.find(
          (bot) => bot.defaultBot !== void 0 || bot.chiefOfStaff === true
        );
        const target = chief ?? bots.find((bot) => bot.welcomed !== true);
        if (!target) return;
        const id = String(target.id ?? "");
        if (!id || target.welcomed === true) return;
        await fetch(`${BOTS_ORIGIN}/api/bots/${id}`, {
          method: "PATCH",
          headers: { ...headers, "content-type": "application/json" },
          body: JSON.stringify({ welcomed: true })
        });
        console.log(`[bots] welcomed ${String(target.name ?? id)}`);
        return;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 1e3));
      }
    }
  }
  function startUniverseBots() {
    if (botsProc && botsProc.exitCode === null) return;
    const root = path2.join(resolveRepoRoot(), "universe-bots");
    const bundled = path2.join(root, "dist-server", "index.js");
    const source = path2.join(root, "server", "universebots.ts");
    let entry;
    let args;
    if (fs2.existsSync(bundled)) {
      entry = bundled;
      args = [];
    } else if (fs2.existsSync(source)) {
      entry = source;
      args = ["--experimental-strip-types", source, "serve"];
    } else {
      console.log("[bots] universe-bots is not built yet; skipping");
      return;
    }
    botsAttempts += 1;
    const dataDir = path2.join(os2.homedir(), ".universe", "bots");
    try {
      fs2.mkdirSync(dataDir, { recursive: true });
      markUniverseBotsWelcomed(dataDir);
      botsProc = utilityProcess.fork(entry, args, {
        cwd: root,
        env: {
          ...process.env,
          UB_PORT: BOTS_PORT,
          UB_STATIC_DIR: path2.join(root, "dist"),
          UB_SKILLS_DIR: path2.join(root, "skills"),
          UB_DATA_DIR: dataDir,
          UB_FRAME_ANCESTORS: botsFrameAncestors(),
          UB_DESKTOP_PARENT: "1"
        },
        stdio: ["ignore", "pipe", "pipe"]
      });
    } catch (err) {
      console.log(`[bots] spawn failed: ${String(err)}`);
      return;
    }
    installBotsOwnerHeader(session.defaultSession);
    installBotsOwnerHeader(session.fromPartition(BOTS_VIEW_PARTITION));
    try {
      botsProc.postMessage({
        type: "universebots:desktop-mutation-token",
        token: BOTS_OWNER_TOKEN
      });
    } catch (err) {
      console.log(`[bots] could not hand over the owner token: ${String(err)}`);
    }
    grantBotsComputer(botsProc);
    void welcomeUniverseBots();
    botsProc.stdout?.on("data", (d) => {
      const line = d.toString().trim();
      if (line) console.log(`[bots] ${line}`);
      const bound = line.match(/universebots server on http:\/\/127\.0\.0\.1:(\d+)/);
      if (bound && bound[1] !== BOTS_PORT) {
        const previous = BOTS_PORT;
        BOTS_PORT = bound[1];
        BOTS_ORIGIN = `http://127.0.0.1:${BOTS_PORT}`;
        setBotsCuaOrigin(BOTS_ORIGIN);
        console.log(`[bots] port changed ${previous} -> ${BOTS_PORT}`);
        installBotsOwnerHeader(session.defaultSession);
        installBotsOwnerHeader(session.fromPartition(BOTS_VIEW_PARTITION));
        botsViewLoaded = false;
        if (botsView && !botsView.webContents.isDestroyed())
          void botsView.webContents.loadURL(BOTS_ORIGIN).catch(() => void 0);
        if (!appQuitting && bridgeStatus === "up") {
          stopBridge();
          bridgeAttempts = 0;
          startBridge();
        }
      }
    });
    botsProc.stderr?.on("data", (d) => {
      const line = d.toString().trim();
      if (line) console.log(`[bots:err] ${line}`);
    });
    botsProc.on("error", (err) => {
      botsProc = null;
      console.log(`[bots] spawn error: ${err.message}`);
    });
    botsProc.on("exit", (code) => {
      botsProc = null;
      if (appQuitting) return;
      if (botsAttempts >= MAX_BOTS_ATTEMPTS) {
        console.log(`[bots] exited (code ${code}) after ${botsAttempts} attempts`);
        return;
      }
      const delay = Math.min(500 * 2 ** (botsAttempts - 1), 1e4);
      console.log(`[bots] exited code=${code}; restart #${botsAttempts} in ${delay}ms`);
      setTimeout(() => {
        if (!appQuitting) startUniverseBots();
      }, delay);
    });
  }
  function stopUniverseBots() {
    const proc = botsProc;
    botsProc = null;
    if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;
    try {
      if (process.platform === "win32" && proc.pid) {
        spawnSync("taskkill", ["/pid", String(proc.pid), "/T", "/F"], {
          stdio: "ignore",
          windowsHide: true
        });
      } else {
        proc.kill("SIGTERM");
        setTimeout(() => {
          try {
            if (proc.exitCode === null && proc.signalCode === null)
              proc.kill("SIGKILL");
          } catch {
          }
        }, 3e3).unref?.();
      }
    } catch {
      try {
        proc.kill();
      } catch {
      }
    }
  }
  function pollHealth() {
    if (bridgeStatus === "starting" || bridgeStatus === "restarting") return;
    const req = net.request(`${BRIDGE_URL}/api/health`);
    req.setHeader("Authorization", `Bearer ${BRIDGE_TOKEN}`);
    req.on("response", (res) => {
      res.on("data", () => {
      });
      res.on("end", () => {
        if (res.statusCode === 200) {
          bridgeAttempts = 0;
          if (bridgeStatus !== "up") pushStatus("up");
        }
      });
    });
    req.on("error", () => {
      if (bridgeStatus === "up") pushStatus("down", "health check failed");
    });
    req.end();
  }
  let mainWindow = null;
  let botsView = null;
  let botsViewLoaded = false;
  let botsSwitcherView = null;
  const BOTS_VIEW_PARTITION = "persist:universebots-embed";
  const TOP_BAR_H = 56;
  function botsPreloadPath() {
    return path2.join(resolveRepoRoot(), "universe-bots", "electron", "preload.cjs");
  }
  function layoutBotsViews() {
    const view = botsView;
    const bar = botsSwitcherView;
    const win = mainWindow;
    if (!win || win.isDestroyed()) return;
    const [width, height] = win.getContentSize();
    const bodyHeight = Math.max(0, height - TOP_BAR_H);
    if (view) view.setBounds({ x: 0, y: TOP_BAR_H, width: Math.max(0, width), height: bodyHeight });
    if (bar) bar.setBounds({ x: 10, y: TOP_BAR_H + 10, width: 248, height: 40 });
  }
  function disposeBotsViews() {
    const views = [botsSwitcherView, botsView];
    botsView = null;
    botsViewLoaded = false;
    botsSwitcherView = null;
    for (const view of views) {
      if (!view) continue;
      try {
        if (mainWindow && !mainWindow.isDestroyed())
          mainWindow.contentView.removeChildView(view);
      } catch {
      }
      try {
        view.webContents.close();
      } catch {
      }
    }
  }
  function createBotsViews() {
    if (botsView || !mainWindow || mainWindow.isDestroyed()) return;
    const preload = botsPreloadPath();
    if (!fs2.existsSync(preload)) {
      console.log("[bots] no preload at " + preload);
      return;
    }
    try {
      const view = new WebContentsView({
        webPreferences: {
          preload,
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
          partition: BOTS_VIEW_PARTITION
        }
      });
      botsView = view;
      const handled = installBotsIpc(view.webContents.ipc, () => mainWindow, () => {
        const mode = cuaConnection()?.mode;
        return mode === "embedded" || mode === "standalone";
      });
      view.webContents.ipc.on("universe:create-bot-via-chat", (event) => {
        if (event.sender !== view.webContents) return;
        mainWindow?.webContents.send("universe:create-bot-via-chat");
      });
      console.log(`[bots] registered ${handled} IPC channels on the view`);
      mainWindow.contentView.addChildView(view);
      layoutBotsViews();
      view.setVisible(false);
      view.webContents.on("did-fail-load", (_e, code, desc) => {
        console.log(`[bots] view load failed: ${code} ${desc}`);
      });
      view.webContents.on("console-message", (_e, level, message) => {
        if (level >= 2) console.log(`[bots:view] ${message}`);
      });
      void view.webContents.loadURL(BOTS_ORIGIN).catch((err) => {
        console.log(`[bots] view load error: ${String(err)}`);
      });
      const bar = new WebContentsView({
        webPreferences: {
          preload: path2.join(__dirname, "bots-switcher.cjs"),
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
          transparent: true
        }
      });
      botsSwitcherView = bar;
      mainWindow.contentView.addChildView(bar);
      layoutBotsViews();
      bar.setVisible(false);
      void bar.webContents.loadURL(
        "data:text/html;charset=utf-8," + encodeURIComponent(BOTS_SWITCHER_HTML)
      );
    } catch (err) {
      disposeBotsViews();
      console.log(`[bots] view creation failed: ${String(err)}`);
    }
  }
  const BOTS_SWITCHER_HTML = `<!doctype html><html><head><meta charset="utf-8"><style>
  html,body{margin:0;padding:0;background:transparent;overflow:hidden}
  .zsb-ws-toggle{display:flex;gap:2px;padding:4px;border-radius:10px;
    background:rgba(22,22,22,.86);border:1px solid #333;backdrop-filter:blur(8px);
    font:12px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
  .zsb-ws-toggle button{appearance:none;border:0;background:transparent;color:#cfcfcf;
    padding:6px 12px;border-radius:7px;cursor:pointer;font:inherit}
  .zsb-ws-toggle button:hover{background:rgba(255,255,255,.08)}
  .zsb-ws-toggle button[data-active="1"]{background:#2b2b2b;color:#fff}
  </style></head><body><nav class="zsb-ws-toggle">
  <button data-surface="universe">Universe</button>
  <button data-surface="studio">Studio</button>
  <button data-surface="bots" data-active="1">Bots</button>
  </nav><script>
  for(const b of document.querySelectorAll('button')){
    b.addEventListener('click',()=>window.botsSwitcher.switchTo(b.dataset.surface));
  }
  </script></body></html>`;
  function applyCsp() {
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
      try {
        const target = new URL(details.url);
        if (target.hostname === "127.0.0.1" && Number(target.port || 80) === Number(BOTS_PORT)) {
          callback({ responseHeaders: details.responseHeaders });
          return;
        }
      } catch {
      }
      const isDevResponse = details.url.startsWith(DEV_URL);
      const scriptSrc = isDevResponse ? "script-src 'self' 'unsafe-inline'" : "script-src 'self'";
      callback({
        responseHeaders: {
          ...details.responseHeaders,
          "Content-Security-Policy": [
            [
              "default-src 'self'",
              scriptSrc,
              "style-src 'self' 'unsafe-inline'",
              `connect-src 'self' ${BRIDGE_URL} ${WS_URL}`,
              // The bots surface frames the universe bots app, which runs on
              // its own loopback port. Without this the frame inherits
              // default-src 'self' and is blocked before it loads.
              `frame-src 'self' http://127.0.0.1:${BOTS_PORT}`,
              "img-src 'self' data: blob:",
              "font-src 'self' data:",
              "worker-src 'self' blob:",
              "media-src 'self' blob:"
            ].join("; ")
          ]
        }
      });
    });
  }
  function devServerAlive(url) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        try {
          req.destroy();
        } catch {
        }
        resolve(ok);
      };
      const req = http.get(url, (res) => {
        res.resume();
        finish(true);
      });
      req.on("error", () => finish(false));
      req.setTimeout(3e3, () => finish(false));
    });
  }
  async function createWindow() {
    let winW = 1440;
    let winH = 900;
    try {
      const area = screen.getPrimaryDisplay().workAreaSize;
      winW = Math.max(1440, Math.min(area.width - 16, 1800));
      winH = Math.max(780, Math.min(area.height - 16, 1e3));
    } catch {
    }
    mainWindow = new BrowserWindow2({
      width: winW,
      height: winH,
      minWidth: 1024,
      minHeight: 700,
      backgroundColor: "#161616",
      // Universe canvas tone (docs/shell-tokens.md)
      show: false,
      // avoid the white flash; shown on ready-to-show
      title: "Universe Agent",
      autoHideMenuBar: true,
      // Universe chrome (Phase 2): the app draws its own title bar
      // (client/src/components/desktop/TopBar.tsx — drag region + window
      // buttons through the universe:win IPC). pollHealth and the bridge
      // token are untouched by this.
      frame: false,
      webPreferences: {
        preload: path2.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        spellcheck: false
      }
    });
    mainWindow.once("ready-to-show", () => mainWindow?.show());
    mainWindow.webContents.on(
      "render-process-gone",
      (_e, details) => console.log(
        `[fatal] renderer gone: reason=${details.reason} exitCode=${details.exitCode}`
      )
    );
    mainWindow.webContents.on(
      "unresponsive",
      () => console.log("[fatal] renderer unresponsive")
    );
    mainWindow.webContents.on("console-message", (e) => {
      const { level, message, source, line } = e;
      if (level >= 2)
        console.log(
          `[renderer:${level === 3 ? "error" : "warn"}] ${message} (${source}:${line})`
        );
    });
    mainWindow.webContents.on(
      "did-fail-load",
      (_e, code, desc, url) => console.log(`[fatal] did-fail-load code=${code} ${desc} ${url}`)
    );
    mainWindow.on("resize", () => layoutBotsViews());
    createBotsViews();
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//i.test(url)) {
        shell2.openExternal(url);
      }
      return { action: "deny" };
    });
    mainWindow.webContents.on("will-navigate", (e, url) => {
      const allowed = url.startsWith("app://universe") || url.startsWith(DEV_URL);
      if (!allowed) {
        console.log(`[security] blocked top-frame navigation to ${url}`);
        e.preventDefault();
      }
    });
    const useDevServer = await devServerAlive(DEV_URL);
    console.log(`[load] probe useDevServer=${useDevServer} url=${DEV_URL}`);
    if (useDevServer || !app.isPackaged) {
      if (!useDevServer) {
        mainWindow.webContents.once("did-fail-load", (_e, code, desc, url) => {
          console.log(
            `[load] dev URL failed (${code} ${desc} ${url}) \u2014 falling back to app://universe/`
          );
          mainWindow?.loadURL("app://universe/");
        });
      }
      mainWindow.loadURL(DEV_URL);
    } else {
      mainWindow.loadURL("app://universe/");
    }
    mainWindow.on("closed", () => {
      disposeBotsViews();
      mainWindow = null;
    });
  }
  const MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".wasm": "application/wasm",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json"
  };
  function registerAppProtocol() {
    const distDir = path2.resolve(__dirname, "..", "dist", "public");
    protocol.handle("app", async (request) => {
      let rel = new URL(request.url).pathname.replace(/^\/+/, "") || "index.html";
      let target = path2.normalize(path2.join(distDir, rel));
      if (!target.startsWith(distDir))
        return new Response("forbidden", { status: 403 });
      if (!fs2.existsSync(target)) {
        if (!path2.extname(rel)) target = path2.join(distDir, "index.html");
        else return new Response("not found", { status: 404 });
      }
      try {
        const data = await fs2.promises.readFile(target);
        return new Response(data, {
          headers: {
            "Content-Type": MIME[path2.extname(target).toLowerCase()] || "application/octet-stream"
          }
        });
      } catch {
        return new Response("read error", { status: 500 });
      }
    });
  }
  ipcMain.handle("universe:open-path", (_e, target) => {
    if (typeof target !== "string" || target.length === 0) return false;
    if (/^https?:\/\//i.test(target)) {
      shell2.openExternal(target);
      return true;
    }
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(target)) return false;
    shell2.showItemInFolder(target);
    return true;
  });
  ipcMain.handle("universe:win", (_e, action) => {
    const w = BrowserWindow2.fromWebContents(_e.sender);
    if (!w) return false;
    if (action === "minimize") w.minimize();
    else if (action === "maximize")
      w.isMaximized() ? w.unmaximize() : w.maximize();
    else if (action === "close") w.close();
    else return false;
    return true;
  });
  ipcMain.handle("universe:copy-text", (_e, text) => {
    try {
      clipboard2.writeText(String(text ?? ""));
      return true;
    } catch {
      return false;
    }
  });
  ipcMain.on("universe:bots-view-visible", (event, visible) => {
    if (BrowserWindow2.fromWebContents(event.sender) !== mainWindow) return;
    const on = visible === true;
    botsView?.setVisible(on);
    botsSwitcherView?.setVisible(on);
    if (on) layoutBotsViews();
  });
  ipcMain.on(
    "universe:bots-view-bounds",
    (event, bounds) => {
      if (BrowserWindow2.fromWebContents(event.sender) !== mainWindow) return;
      const view = botsView;
      const win = mainWindow;
      if (!view || !win || win.isDestroyed()) return;
      if (!bounds || typeof bounds.width !== "number" || typeof bounds.height !== "number") return;
      const [contentWidth, contentHeight] = win.getContentSize();
      view.setBounds({
        x: Math.max(0, Math.min(contentWidth, Math.round(bounds.x))),
        y: Math.max(0, Math.min(contentHeight, Math.round(bounds.y))),
        width: Math.max(0, Math.min(contentWidth, Math.round(bounds.width))),
        height: Math.max(0, Math.min(contentHeight, Math.round(bounds.height)))
      });
      if (botsSwitcherView)
        botsSwitcherView.setBounds({ x: 10, y: Math.round(bounds.y) + 10, width: 248, height: 40 });
    }
  );
  ipcMain.on("universe:bots-switch", (event, surface) => {
    if (event.sender !== botsSwitcherView?.webContents) return;
    if (surface !== "universe" && surface !== "studio" && surface !== "bots") return;
    mainWindow?.webContents.send("universe:navigate-surface", surface);
  });
  ipcMain.handle("universe:bridge-info", () => ({
    port: BRIDGE_PORT,
    url: BRIDGE_URL,
    status: bridgeStatus,
    token: BRIDGE_TOKEN
  }));
  ipcMain.handle("universe:pick-file", async (_e) => {
    const w = BrowserWindow2.fromWebContents(_e.sender) ?? BrowserWindow2.getAllWindows()[0];
    const res = await dialog2.showOpenDialog(w, {
      title: "Choose a file to attach",
      properties: ["openFile"]
    });
    if (res.canceled || !res.filePaths.length) return null;
    return res.filePaths[0];
  });
  ipcMain.handle(
    "universe:paste-clipboard-media",
    async (_e) => {
      try {
        const img = clipboard2.readImage();
        if (img.isEmpty()) return null;
        const dir = path2.join(os2.tmpdir(), "universe-attachments");
        await fs2.promises.mkdir(dir, { recursive: true });
        const p = path2.join(dir, `clipboard-${Date.now()}.png`);
        await fs2.promises.writeFile(p, img.toPNG());
        return p;
      } catch {
        return null;
      }
    }
  );
  ipcMain.handle("universe:pick-folder", async (_e) => {
    const w = BrowserWindow2.fromWebContents(_e.sender) ?? BrowserWindow2.getAllWindows()[0];
    const res = await dialog2.showOpenDialog(w, {
      title: "Choose Universe work folder",
      properties: ["openDirectory", "createDirectory"]
    });
    if (res.canceled || !res.filePaths.length) return null;
    return res.filePaths[0];
  });
  ipcMain.handle("universe:bridge-restart", () => {
    if (appQuitting) return false;
    if (bridgeStatus === "starting" || bridgeStatus === "restarting")
      return false;
    stopBridge();
    bridgeAttempts = 0;
    startBridge();
    return true;
  });
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(() => {
    applyCsp();
    registerAppProtocol();
    startBridge();
    startUniverseBots();
    void startBotsCua(resolveRepoRoot(), BOTS_ORIGIN, (connection2) => {
      const ready = connection2?.mode === "embedded" || connection2?.mode === "standalone";
      if (botsView && !botsView.webContents.isDestroyed())
        botsView.webContents.send("desktop:capabilities-changed", { localComputerReady: ready });
      if (ready) grantBotsComputer(botsProc);
    });
    healthTimer = setInterval(pollHealth, HEALTH_POLL_MS);
    void createWindow();
    app.on("activate", () => {
      if (BrowserWindow2.getAllWindows().length === 0) void createWindow();
    });
  });
  app.on("before-quit", () => {
    appQuitting = true;
    if (healthTimer) clearInterval(healthTimer);
    disposeBotsViews();
    void stopBotsCua();
    stopBridge();
    stopUniverseBots();
  });
  app.on("window-all-closed", () => {
    appQuitting = true;
    disposeBotsViews();
    void stopBotsCua();
    stopBridge();
    stopUniverseBots();
    if (process.platform !== "darwin") app.quit();
  });
}
