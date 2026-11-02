/**
 * electron/main.ts — Universe Desktop: window + Python bridge supervisor.
 *
 * One window (1440×900, #0b0d11 — never a white flash) loading the React
 * client, plus one supervised child process: the Universe FastAPI bridge
 * (`python -m universe_desktop_bridge`, 127.0.0.1:8738).
 *
 * Bridge supervision (plan §5):
 *   - spawns the bridge at app start; kills it on quit (never orphaned)
 *   - restarts it on exit with capped backoff (500ms ×2 → 10s, 5 attempts)
 *   - health-polls /api/health every 30s and pushes status to the renderer
 *     so the UI can show its offline banner / backend state
 *
 * Security posture: contextIsolation on, nodeIntegration off, sandbox,
 * a single narrow preload, and a CSP that only talks to the bridge.
 */
import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  ipcMain,
  net,
  protocol,
  session,
  shell,
} from "electron";
import { ChildProcess, spawn, spawnSync } from "node:child_process";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import crypto from "node:crypto";

// ---- constants ------------------------------------------------------------

const BRIDGE_PORT = process.env.UNIVERSE_BRIDGE_PORT || process.env.GALAXY_BRIDGE_PORT || "8738";
const BRIDGE_URL = `http://127.0.0.1:${BRIDGE_PORT}`;
const WS_URL = `ws://127.0.0.1:${BRIDGE_PORT}`;
const DEV_URL = process.env.ELECTRON_START_URL || "http://127.0.0.1:5199";
const HEALTH_POLL_MS = 30_000;
const MAX_BRIDGE_ATTEMPTS = 5;

/** Per-launch bearer token for the bridge. Every renderer HTTP/WS request
 *  must carry it; without it any local process or webpage could drive the
 *  bridge (approve consents, spawn MCP subprocesses, start goals). */
const BRIDGE_TOKEN = crypto.randomBytes(32).toString("base64url");

/** The Universe backend repo root (the folder containing universe_desktop_bridge/).
 *  dev:  <repo>/universe-agent-ui/dist-electron -> up 3
 *  packaged builds resolve this at packaging time (P9). */
function resolveRepoRoot(): string {
  // UNIVERSE_ROOT wins first: the packaged installer can't know where the
  // user keeps the Universe source checkout, so the README documents it.
  const candidates = [
    process.env.UNIVERSE_ROOT || process.env.GALAXY_ROOT || "",
    path.resolve(__dirname, "..", ".."),
    path.resolve(__dirname, "..", "..", ".."),
  ].filter(Boolean);
  for (const dir of candidates) {
    if (fs.existsSync(path.join(dir, "universe_desktop_bridge"))) return dir;
  }
  return candidates[candidates.length - 1] || process.cwd();
}

// app:// serves the built client (a real origin, so the bridge's CORS can
// stay strict instead of allowing file:// "null" origins)
protocol.registerSchemesAsPrivileged([
  {
    scheme: "app",
    privileges: {
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      standard: true,
    },
  },
]);

/* Renderer crash guard: on some Windows GPU/driver combos Chromium fails
 * GPU init ("GPU state invalid") and the window dies instantly, exit 0,
 * no error surfaced. The app is DOM/canvas 2D + one WebGL universe — it does
 * not need GPU compositing; software rendering is fine and rock solid.
 * Force-disable it unless the user opts back in with UNIVERSE_GPU=1.
 * Chromium 139+ dropped the automatic SwiftShader fallback: without this
 * switch a GPU-less renderer cannot create ANY WebGL context and the 3D
 * memory universe dies. */
if (!process.env.UNIVERSE_GPU && !process.env.GALAXY_GPU) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu-compositing");
  app.commandLine.appendSwitch("enable-unsafe-swiftshader");
}

/* EPIPE guard: on Windows the main process's stdout is a pipe to whoever
 * launched us (npm/concurrently/terminal). When that parent dies, every
 * console.log throws "EPIPE: broken pipe, write" as an UNCAUGHT exception
 * and Electron shows the fatal JS-error dialog while the app itself is
 * perfectly fine. A broken stdout must never kill the desktop app:
 *  - a top-level uncaughtException filter for EPIPE only (real bugs still
 *    crash loudly through the default path) */
process.on("uncaughtException", (err: NodeJS.ErrnoException) => {
  if (err?.code === "EPIPE") return; // broken stdout/stderr pipe — benign
  // not an EPIPE: preserve the default crash behavior
  // eslint-disable-next-line no-console
  try {
    console.error("[fatal] uncaught:", err);
  } catch {
    /* even the error path can be broken; nothing left to do */
  }
  process.exitCode = 1;
});

// ---- single instance ------------------------------------------------------

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  bootstrapDesktop();
  /* deep link: a second `electron . --goal=<id>` (or any argv carrying
   * goal=<id>) focuses the window and opens that conversation — used by
   * tooling and the task-share flow; the second process itself exits. */
  app.on("second-instance", (_evt, argv) => {
    const m = argv
      .map(a => a.match(/(?:^|[?&-])goal=([A-Za-z0-9_-]+)/))
      .find(Boolean);
    console.log(`[deep-link] second-instance argv=${JSON.stringify(argv)} goal=${m ? m[1] : null}`);
    const w = BrowserWindow.getAllWindows()[0];
    if (!w) return;
    if (w.isMinimized()) w.restore();
    w.show();
    w.focus();
    if (m) {
      const url = app.isPackaged
        ? `app://universe/?goal=${m[1]}`
        : `${DEV_URL}/?goal=${m[1]}`;
      void w.loadURL(url);
    }
  });
}

/** Everything is wired inside the lock holder so a second instance exits
 *  without ever spawning a bridge or a window. */
function bootstrapDesktop(): void {
  // ---- bridge supervision ----------------------------------------------------

  type BridgeStatus = "starting" | "up" | "restarting" | "down" | "failed";

  let appQuitting = false;
  let bridgeProc: ChildProcess | null = null;
  let bridgeAttempts = 0;
  let bridgeStatus: BridgeStatus = "starting";
  let healthTimer: NodeJS.Timeout | null = null;

  function pushStatus(status: BridgeStatus, detail = "") {
    bridgeStatus = status;
    for (const w of BrowserWindow.getAllWindows()) {
      w.webContents.send("universe:bridge-status", {
        status,
        detail,
        port: BRIDGE_PORT,
      });
    }
  }

  function startBridge(): void {
    if (
      app.isPackaged &&
      !fs.existsSync(path.join(resolveRepoRoot(), "universe_desktop_bridge"))
    ) {
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
          PYTHONUNBUFFERED: "1",
        },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
    } catch (err) {
      pushStatus("failed", `spawn failed: ${String(err)}`);
      return;
    }
    bridgeProc.stdout?.on("data", (d: Buffer) => {
      const line = d.toString().trim();
      if (line) console.log(`[bridge] ${line}`);
      if (line.includes("Uvicorn running")) pushStatus("up");
    });
    bridgeProc.stderr?.on("data", (d: Buffer) => {
      const line = d.toString().trim();
      if (line) console.log(`[bridge:err] ${line}`);
    });
    // async spawn errors (ENOENT python, EACCES, …) never reach the sync
    // try/catch above — they arrive here. Without this handler a missing
    // interpreter leaves the UI stuck on "starting" forever.
    bridgeProc.on("error", (err: Error) => {
      bridgeProc = null;
      if (appQuitting) return;
      pushStatus("failed", `bridge spawn error: ${err.message}`);
    });
    bridgeProc.on("exit", code => {
      bridgeProc = null;
      if (appQuitting) return;
      if (bridgeAttempts >= MAX_BRIDGE_ATTEMPTS) {
        pushStatus(
          "failed",
          `bridge exited (code ${code}) after ${bridgeAttempts} attempts`
        );
        return;
      }
      const delay = Math.min(500 * 2 ** (bridgeAttempts - 1), 10_000);
      console.log(
        `[bridge] exited code=${code}; restart #${bridgeAttempts} in ${delay}ms`
      );
      pushStatus("restarting", `restart in ${delay}ms`);
      setTimeout(() => {
        if (!appQuitting) startBridge();
      }, delay);
    });
  }

  function stopBridge(): void {
    const proc = bridgeProc;
    bridgeProc = null;
    if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;
    try {
      if (process.platform === "win32" && proc.pid) {
        // Windows has no process groups via kill(): taskkill /T takes the
        // whole tree (uvicorn reloader children) so nothing is orphaned.
        spawnSync("taskkill", ["/pid", String(proc.pid), "/T", "/F"], {
          stdio: "ignore",
          windowsHide: true,
        });
      } else {
        proc.kill("SIGTERM");
        // SIGKILL fallback after a grace period if it ignores SIGTERM.
        setTimeout(() => {
          try {
            if (proc.exitCode === null && proc.signalCode === null)
              proc.kill("SIGKILL");
          } catch {
            /* already gone */
          }
        }, 3000).unref?.();
      }
    } catch {
      try {
        proc.kill();
      } catch {
        /* already gone */
      }
    }
  }

  function pollHealth(): void {
    // skip verdicts during the start/restart window: a poll racing the
    // spawn would flip a healthy-restarting bridge to "down".
    if (bridgeStatus === "starting" || bridgeStatus === "restarting") return;
    // the bridge's auth middleware requires the token on every route — a
    // bare poll got 401 forever, so the UI flipped to "down" 30s after a
    // healthy start and bridgeAttempts never reset (audit 2026-09)
    const req = net.request(`${BRIDGE_URL}/api/health`);
    req.setHeader("Authorization", `Bearer ${BRIDGE_TOKEN}`);
    req.on("response", res => {
      // draining
      res.on("data", () => {});
      res.on("end", () => {
        if (res.statusCode === 200) {
          // a transient blip must not permanently consume restart attempts
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

  // ---- window ----------------------------------------------------------------

  let mainWindow: BrowserWindow | null = null;

  function applyCsp(): void {
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
      // Dev vs packaged CSP. @vitejs/plugin-react injects an INLINE preamble
      // into every JSX module for Fast Refresh; a strict `script-src 'self'`
      // blocks it, React throws "can't detect preamble" and the window goes
      // blank. The vite server is trusted localhost, so dev responses allow
      // 'unsafe-inline' — the packaged app:// build (no inline scripts) keeps
      // the strict policy.
      const isDevResponse = details.url.startsWith(DEV_URL);
      const scriptSrc = isDevResponse
        ? "script-src 'self' 'unsafe-inline'"
        : "script-src 'self'";
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
              "media-src 'self' blob:",
            ].join("; "),
          ],
        },
      });
    });
  }

  /** Probe the vite dev server: if it answers, we're in dev mode. This
   *  replaces the NODE_ENV check, which is unreliable through cmd.exe npm
   *  scripts on Windows and silently loaded the packaged build in dev. */
  function devServerAlive(url: string): Promise<boolean> {
    return new Promise(resolve => {
      // Node's http, never Electron's net: net resolves through the system
      // proxy, and a proxy without a localhost bypass fails the 127.0.0.1
      // probe — which silently loaded the stale packaged build in dev.
      let settled = false;
      const finish = (ok: boolean) => {
        if (settled) return;
        settled = true;
        try {
          req.destroy();
        } catch {
          /* noop */
        }
        resolve(ok);
      };
      const req = http.get(url, res => {
        res.resume(); // drain so the socket can close
        finish(true);
      });
      req.on("error", () => finish(false));
      req.setTimeout(3000, () => finish(false));
    });
  }

  async function createWindow(): Promise<void> {
    mainWindow = new BrowserWindow({
      width: 1440,
      height: 900,
      minWidth: 1024,
      minHeight: 700,
      backgroundColor: "#161616", // ZCode canvas tone (docs/zcode-tokens.md)
      show: false, // avoid the white flash; shown on ready-to-show
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
        spellcheck: false,
      },
    });

    mainWindow.once("ready-to-show", () => mainWindow?.show());

    // crash forensics: the WHY of every renderer death, loudly, with reason
    mainWindow.webContents.on("render-process-gone", (_e, details) =>
      console.log(
        `[fatal] renderer gone: reason=${details.reason} exitCode=${details.exitCode}`
      )
    );
    mainWindow.webContents.on("unresponsive", () =>
      console.log("[fatal] renderer unresponsive")
    );
    // surface renderer JS errors in the main log — a blank window with a
    // silent console must never happen again (CSP preamble bug taught this)
    mainWindow.webContents.on("console-message", e => {
      const { level, message, source, line } = e as unknown as {
        level: number;
        message: string;
        source: string;
        line: number;
      };
      if (level >= 2)
        console.log(
          `[renderer:${level === 3 ? "error" : "warn"}] ${message} (${source}:${line})`
        );
    });
    mainWindow.webContents.on("did-fail-load", (_e, code, desc, url) =>
      console.log(`[fatal] did-fail-load code=${code} ${desc} ${url}`)
    );

    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
      // the only outbound navigation allowed is real http(s) links handed to
      // the system browser. Scheme allowlist (audit 2026-09): model-rendered
      // markdown can carry links, and shell.openExternal on an arbitrary
      // scheme (ms-msdt:, search-ms:, file:, custom protocol handlers)
      // invokes registered OS handlers — mirror the universe:open-path check.
      if (/^https?:\/\//i.test(url)) {
        shell.openExternal(url);
      }
      return { action: "deny" };
    });

    // defense in depth (audit 2026-09): nothing may navigate the top frame
    // away from our origins — a remote page loaded WITH the preload attached
    // would receive the bridge token via the universe:bridge-info IPC.
    mainWindow.webContents.on("will-navigate", (e, url) => {
      const allowed = url.startsWith("app://universe") || url.startsWith(DEV_URL);
      if (!allowed) {
        console.log(`[security] blocked top-frame navigation to ${url}`);
        e.preventDefault();
      }
    });

    // Load the client. NEVER rely on NODE_ENV to pick the source: npm scripts
    // on Windows (cmd.exe) can't set it inline, so an env-based check silently
    // loads the packaged app:// build in dev mode — and /index.html isn't a
    // client route, leaving the window on a 404. Probe the dev server instead:
    // if vite answers, dev mode is intended.
    const useDevServer = await devServerAlive(DEV_URL);
    console.log(`[load] probe useDevServer=${useDevServer} url=${DEV_URL}`);
    if (useDevServer || !app.isPackaged) {
      // dev launch ALWAYS prefers the vite server — the probe has false
      // negatives (proxy weirdness, slow first answer), and silently
      // loading the stale packaged build in dev cost a whole day once.
      // The packaged build stays the fallback when the URL truly fails.
      if (!useDevServer) {
        mainWindow.webContents.once("did-fail-load", (_e, code, desc, url) => {
          console.log(
            `[load] dev URL failed (${code} ${desc} ${url}) — falling back to app://universe/`
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

  // app://universe/* -> the vite build output (dist/public)
  const MIME: Record<string, string> = {
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
    ".map": "application/json",
  };

  function registerAppProtocol(): void {
    const distDir = path.resolve(__dirname, "..", "dist", "public");
    protocol.handle("app", async request => {
      let rel =
        new URL(request.url).pathname.replace(/^\/+/, "") || "index.html";
      let target = path.normalize(path.join(distDir, rel));
      if (!target.startsWith(distDir))
        return new Response("forbidden", { status: 403 });
      if (!fs.existsSync(target)) {
        // SPA fallback: client-side routes (/universe, …) all render from index.html
        if (!path.extname(rel)) target = path.join(distDir, "index.html");
        else return new Response("not found", { status: 404 });
      }
      try {
        const data = await fs.promises.readFile(target);
        return new Response(data, {
          headers: {
            "Content-Type":
              MIME[path.extname(target).toLowerCase()] ||
              "application/octet-stream",
          },
        });
      } catch {
        return new Response("read error", { status: 500 });
      }
    });
  }

  // ---- narrow IPC ------------------------------------------------------------

  ipcMain.handle("universe:open-path", (_e, target: string) => {
    if (typeof target !== "string" || target.length === 0) return false;
    // URLs: only http(s) may open externally — shell.openExternal on an
    // arbitrary scheme (file://, ms-*, custom protocol handlers) can execute
    // programs on the user's machine
    if (/^https?:\/\//i.test(target)) {
      shell.openExternal(target);
      return true;
    }
    // anything else that looks like a URL is refused outright
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(target)) return false;
    shell.showItemInFolder(target);
    return true;
  });

  ipcMain.handle("universe:win", (_e, action: string) => {
    const w = BrowserWindow.fromWebContents(_e.sender);
    if (!w) return false;
    if (action === "minimize") w.minimize();
    else if (action === "maximize")
      w.isMaximized() ? w.unmaximize() : w.maximize();
    else if (action === "close") w.close();
    else return false;
    return true;
  });

  // Copy (the message Copy chips): the MAIN process owns the OS clipboard —
  // navigator.clipboard.writeText silently rejects in some webviews, and
  // the buttons looked dead there (user bug 2026-10-30)
  ipcMain.handle("universe:copy-text", (_e, text: unknown) => {
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
    token: BRIDGE_TOKEN,
  }));

  // native file picker (composer "+ Add file"): the renderer gets ONE
  // chosen absolute path (or null on cancel) — no browsing API, no dirs.
  ipcMain.handle("universe:pick-file", async (_e): Promise<string | null> => {
    const w =
      BrowserWindow.fromWebContents(_e.sender) ??
      BrowserWindow.getAllWindows()[0];
    const res = await dialog.showOpenDialog(w!, {
      title: "Choose a file to attach",
      properties: ["openFile"],
    });
    if (res.canceled || !res.filePaths.length) return null;
    return res.filePaths[0];
  });

  // pasted-image media (composer Ctrl+V of a screenshot): the renderer's
  // clipboardData cannot produce a path for a bitmap paste (paste of a
  // raw image has no file origin, and Electron 43 removed File.path) —
  // the MAIN process owns the clipboard: one image → one PNG in the OS
  // temp dir → the path back. Explorer-copied media (image/video with a
  // real path) resolve in the renderer via webUtils.getPathForFile and
  // never reach this handler.
  ipcMain.handle(
    "universe:paste-clipboard-media",
    async (_e): Promise<string | null> => {
      try {
        const img = clipboard.readImage();
        if (img.isEmpty()) return null;
        const dir = path.join(os.tmpdir(), "universe-attachments");
        await fs.promises.mkdir(dir, { recursive: true });
        const p = path.join(dir, `clipboard-${Date.now()}.png`);
        await fs.promises.writeFile(p, img.toPNG());
        return p;
      } catch {
        return null; // clipboard unreadable — the renderer explains honestly
      }
    }
  );

  // native folder picker (Settings → General → Work folder). The renderer
  // never gets a path-browsing node API — one narrow invoke returning the
  // chosen absolute path (or null on cancel).
  ipcMain.handle("universe:pick-folder", async (_e): Promise<string | null> => {
    const w =
      BrowserWindow.fromWebContents(_e.sender) ??
      BrowserWindow.getAllWindows()[0];
    const res = await dialog.showOpenDialog(w!, {
      title: "Choose Universe work folder",
      properties: ["openDirectory", "createDirectory"],
    });
    if (res.canceled || !res.filePaths.length) return null;
    return res.filePaths[0];
  });

  // manual retry affordance for the OfflineBanner: reset the attempt counter
  // (a transient blip may have consumed it) and spawn immediately, unless a
  // spawn is already in flight or the app is quitting.
  ipcMain.handle("universe:bridge-restart", () => {
    if (appQuitting) return false;
    if (bridgeStatus === "starting" || bridgeStatus === "restarting")
      return false;
    stopBridge();
    bridgeAttempts = 0;
    startBridge();
    return true;
  });

  // ---- app lifecycle (inside the single-instance holder) ----------------------

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
