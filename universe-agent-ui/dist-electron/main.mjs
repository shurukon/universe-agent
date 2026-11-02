import { createRequire as __cr } from 'node:module'; import { fileURLToPath as __f2p } from 'node:url'; import * as __p from 'node:path'; const require = __cr(import.meta.url); const __dirname = __p.dirname(__f2p(import.meta.url));

// electron/main.ts
import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  ipcMain,
  net,
  protocol,
  session,
  shell
} from "electron";
import { spawn, spawnSync } from "node:child_process";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import crypto from "node:crypto";
var BRIDGE_PORT = process.env.UNIVERSE_BRIDGE_PORT || process.env.GALAXY_BRIDGE_PORT || "8738";
var BRIDGE_URL = `http://127.0.0.1:${BRIDGE_PORT}`;
var WS_URL = `ws://127.0.0.1:${BRIDGE_PORT}`;
var DEV_URL = process.env.ELECTRON_START_URL || "http://127.0.0.1:5199";
var HEALTH_POLL_MS = 3e4;
var MAX_BRIDGE_ATTEMPTS = 5;
var BRIDGE_TOKEN = crypto.randomBytes(32).toString("base64url");
function resolveRepoRoot() {
  const candidates = [
    process.env.UNIVERSE_ROOT || process.env.GALAXY_ROOT || "",
    path.resolve(__dirname, "..", ".."),
    path.resolve(__dirname, "..", "..", "..")
  ].filter(Boolean);
  for (const dir of candidates) {
    if (fs.existsSync(path.join(dir, "universe_desktop_bridge"))) return dir;
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
    const w = BrowserWindow.getAllWindows()[0];
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
    for (const w of BrowserWindow.getAllWindows()) {
      w.webContents.send("universe:bridge-status", {
        status,
        detail,
        port: BRIDGE_PORT
      });
    }
  }
  function startBridge() {
    if (app.isPackaged && !fs.existsSync(path.join(resolveRepoRoot(), "universe_desktop_bridge"))) {
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
  function applyCsp() {
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
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
    mainWindow = new BrowserWindow({
      width: 1440,
      height: 900,
      minWidth: 1024,
      minHeight: 700,
      backgroundColor: "#161616",
      // ZCode canvas tone (docs/zcode-tokens.md)
      show: false,
      // avoid the white flash; shown on ready-to-show
      title: "Universe Agent",
      autoHideMenuBar: true,
      // ZCode-literal chrome (Phase 2): the app draws its own title bar
      // (client/src/components/desktop/TopBar.tsx — drag region + window
      // buttons through the universe:win IPC). pollHealth and the bridge
      // token are untouched by this.
      frame: false,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
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
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//i.test(url)) {
        shell.openExternal(url);
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
    const distDir = path.resolve(__dirname, "..", "dist", "public");
    protocol.handle("app", async (request) => {
      let rel = new URL(request.url).pathname.replace(/^\/+/, "") || "index.html";
      let target = path.normalize(path.join(distDir, rel));
      if (!target.startsWith(distDir))
        return new Response("forbidden", { status: 403 });
      if (!fs.existsSync(target)) {
        if (!path.extname(rel)) target = path.join(distDir, "index.html");
        else return new Response("not found", { status: 404 });
      }
      try {
        const data = await fs.promises.readFile(target);
        return new Response(data, {
          headers: {
            "Content-Type": MIME[path.extname(target).toLowerCase()] || "application/octet-stream"
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
      shell.openExternal(target);
      return true;
    }
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(target)) return false;
    shell.showItemInFolder(target);
    return true;
  });
  ipcMain.handle("universe:win", (_e, action) => {
    const w = BrowserWindow.fromWebContents(_e.sender);
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
      clipboard.writeText(String(text ?? ""));
      return true;
    } catch {
      return false;
    }
  });
  ipcMain.handle("universe:bridge-info", () => ({
    port: BRIDGE_PORT,
    url: BRIDGE_URL,
    status: bridgeStatus,
    token: BRIDGE_TOKEN
  }));
  ipcMain.handle("universe:pick-file", async (_e) => {
    const w = BrowserWindow.fromWebContents(_e.sender) ?? BrowserWindow.getAllWindows()[0];
    const res = await dialog.showOpenDialog(w, {
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
        const img = clipboard.readImage();
        if (img.isEmpty()) return null;
        const dir = path.join(os.tmpdir(), "universe-attachments");
        await fs.promises.mkdir(dir, { recursive: true });
        const p = path.join(dir, `clipboard-${Date.now()}.png`);
        await fs.promises.writeFile(p, img.toPNG());
        return p;
      } catch {
        return null;
      }
    }
  );
  ipcMain.handle("universe:pick-folder", async (_e) => {
    const w = BrowserWindow.fromWebContents(_e.sender) ?? BrowserWindow.getAllWindows()[0];
    const res = await dialog.showOpenDialog(w, {
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
    healthTimer = setInterval(pollHealth, HEALTH_POLL_MS);
    void createWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) void createWindow();
    });
  });
  app.on("before-quit", () => {
    appQuitting = true;
    if (healthTimer) clearInterval(healthTimer);
    stopBridge();
  });
  app.on("window-all-closed", () => {
    appQuitting = true;
    stopBridge();
    if (process.platform !== "darwin") app.quit();
  });
}
