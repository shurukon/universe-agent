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
  screen,
  session,
  shell,
  utilityProcess,
  WebContentsView,
} from "electron";
import { ChildProcess, spawn, spawnSync } from "node:child_process";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import fs from "node:fs";
import crypto from "node:crypto";
import { installBotsIpc } from "./bots-ipc";

// ---- constants ------------------------------------------------------------

const BRIDGE_PORT = process.env.UNIVERSE_BRIDGE_PORT || process.env.GALAXY_BRIDGE_PORT || "8738";
const BRIDGE_URL = `http://127.0.0.1:${BRIDGE_PORT}`;
const WS_URL = `ws://127.0.0.1:${BRIDGE_PORT}`;
const DEV_URL = process.env.ELECTRON_START_URL || "http://127.0.0.1:5199";
const HEALTH_POLL_MS = 30_000;
const MAX_BRIDGE_ATTEMPTS = 5;
/** universe bots gets fewer: it can refuse to start because another
 *  OpenMausBot already holds its data directory, and a restart loop would
 *  only spin. */
const MAX_BOTS_ATTEMPTS = 3;

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
          // the bots surface asks the bridge where universe bots lives
          UNIVERSE_BOTS_ORIGIN: BOTS_ORIGIN,
          UNIVERSE_BOTS_DATA_DIR: path.join(os.homedir(), ".universe", "bots"),
          // the bridge is this app's own process, so it carries the same
          // capability the harness gives the desktop: the routes that change
          // something refuse a request without it.
          UNIVERSE_BOTS_OWNER_TOKEN: BOTS_OWNER_TOKEN,
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

  // ---- universe bots supervision ---------------------------------------------
  //
  // universe bots is OpenMausBot, renamed, vendored at universe-bots/. It runs
  // as its own harness on 127.0.0.1:8799 and the Bots surface frames it.
  //
  // The harness trusts only its own desktop parent: it refuses a browser that
  // does not carry the owner capability, and it trusts only the origins it is
  // told about. So this app does three things for it - it names itself as an
  // allowed frame ancestor, it runs the harness as a UTILITY PROCESS (so the
  // harness gets a parent message port), and it hands the harness the owner
  // token and adds it to every request the embedded UI makes.
  //
  // A missing build is reported and never fatal - the rest of the Universe
  // does not depend on it.

  let BOTS_PORT = process.env.UNIVERSE_BOTS_PORT || "8799";
  let BOTS_ORIGIN = `http://127.0.0.1:${BOTS_PORT}`;
  /** The harness's owner capability: a browser without it is asked to pair. */
  const BOTS_OWNER_HEADER = "X-UB-Desktop-Owner";
  const BOTS_OWNER_TOKEN = crypto.randomBytes(32).toString("base64url");
  let botsProc: ChildProcess | null = null;
  let botsAttempts = 0;

  /** The origin the renderer runs on, which is the frame the harness may be
   *  shown in. */
  function botsFrameAncestors(): string {
    const dev = "http://127.0.0.1:5199";
    const packaged = "app://universe";
    return (
      process.env.UNIVERSE_BOTS_FRAME_ANCESTORS ||
      (app.isPackaged ? packaged : dev)
    );
  }

  function installBotsOwnerHeader(target: Electron.Session): void {
    // Chromium adds this to every request the session makes, on http and ws
    // alike, so the embedded UI is the owner without a pairing code. The
    // view runs in its own partition, so each session that can reach the
    // harness needs the hook — without it the harness serves its frozen
    // non-owner "Opening My Cloud" page instead of the real app.
    target.webRequest.onBeforeSendHeaders((details, callback) => {
      const headers = { ...details.requestHeaders };
      try {
        const target = new URL(details.url);
        if (
          target.hostname === "127.0.0.1" &&
          Number(target.port || 80) === Number(BOTS_PORT)
        ) {
          headers[BOTS_OWNER_HEADER] = BOTS_OWNER_TOKEN;
        }
      } catch {
        /* not a URL we can place */
      }
      callback({ requestHeaders: headers });
    });
  }

  /** The first-run hints of the universe bots app: the welcome flow, the
   *  guided tour, and the launch notices. This desktop goes straight to the
   *  app, so every one of them is recorded as seen. */
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
    "pro-introduction-dismissed-v3",
  ];

  /** The universe bots welcome is a first-run screen. This desktop shows the
   *  app straight away, so the record that marks it done is written into the
   *  data dir before the harness starts; the harness reads it and goes to the
   *  home screen. Nothing in the app's own code changes, and deleting that
   *  one file brings the welcome back. */
  function markUniverseBotsWelcomed(dataDir: string): void {
    const file = path.join(dataDir, "config.json");
    let config: Record<string, unknown> = {};
    try {
      const parsed: unknown = JSON.parse(fs.readFileSync(file, "utf8"));
      if (parsed && typeof parsed === "object") {
        config = parsed as Record<string, unknown>;
      }
    } catch {
      // No file yet, or one we cannot read: start from an empty record.
    }
    const record = (
      config.onboarding && typeof config.onboarding === "object"
        ? config.onboarding
        : {}
    ) as Record<string, unknown>;
    const hints = Array.isArray(record.hintsSeen)
      ? record.hintsSeen.filter((hint): hint is string => typeof hint === "string")
      : [];
    const merged = [...new Set([...hints, ...UNIVERSE_BOTS_FIRST_RUN_HINTS])];
    if (record.completedAt && typeof record.version === "number" && record.version >= 1) {
      if (merged.length === hints.length) return;
    }
    config.onboarding = {
      ...record,
      completedAt:
        typeof record.completedAt === "string" && record.completedAt
          ? record.completedAt
          : new Date().toISOString(),
      version: 1,
      hintsSeen: merged,
    };
    fs.writeFileSync(file, `${JSON.stringify(config, null, 2)}\n`, "utf8");
  }

  /** The app's own per-bot welcome card is a first-run screen too, and it
   *  waits for the app-level welcome to be out of the way — so finishing
   *  that one lets it through. The Universe goes straight to the app, so
   *  the place's own Universe Bots is marked as welcomed as soon as the
   *  harness answers. */
  async function welcomeUniverseBots(): Promise<void> {
    const headers = { [BOTS_OWNER_HEADER]: BOTS_OWNER_TOKEN };
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        const response = await fetch(`${BOTS_ORIGIN}/api/bots`, { headers });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        // The view is created before the harness finishes booting, so its
        // first navigation can lose the race. Now that the server answers,
        // load it exactly once.
        if (botsView && !botsView.webContents.isDestroyed() && !botsViewLoaded) {
          botsViewLoaded = true;
          void botsView.webContents.loadURL(BOTS_ORIGIN).catch(() => {
            botsViewLoaded = false;
          });
        }
        const body = (await response.json()) as {
          bots?: Array<Record<string, unknown>>;
        };
        const bots = Array.isArray(body.bots) ? body.bots : [];
        const chief = bots.find(
          bot => bot.defaultBot !== undefined || bot.chiefOfStaff === true,
        );
        const target = chief ?? bots.find(bot => bot.welcomed !== true);
        if (!target) return;
        const id = String(target.id ?? "");
        if (!id || target.welcomed === true) return;
        await fetch(`${BOTS_ORIGIN}/api/bots/${id}`, {
          method: "PATCH",
          headers: { ...headers, "content-type": "application/json" },
          body: JSON.stringify({ welcomed: true }),
        });
        console.log(`[bots] welcomed ${String(target.name ?? id)}`);
        return;
      } catch {
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }
  }

  function startUniverseBots(): void {
    if (botsProc && botsProc.exitCode === null) return;
    const root = path.join(resolveRepoRoot(), "universe-bots");
    const bundled = path.join(root, "dist-server", "index.js");
    const source = path.join(root, "server", "universebots.ts");
    let entry: string;
    let args: string[];
    if (fs.existsSync(bundled)) {
      entry = bundled;
      args = [];
    } else if (fs.existsSync(source)) {
      entry = source;
      args = ["--experimental-strip-types", source, "serve"];
    } else {
      console.log("[bots] universe-bots is not built yet; skipping");
      return;
    }
    botsAttempts += 1;
    // The universe bots world lives under the user's home, in the same
    // ".universe" folder the rest of Universe keeps its own state in.
    const dataDir = path.join(os.homedir(), ".universe", "bots");
    try {
      fs.mkdirSync(dataDir, { recursive: true });
      markUniverseBotsWelcomed(dataDir);
      botsProc = utilityProcess.fork(entry, args, {
        cwd: root,
        env: {
          ...process.env,
          UB_PORT: BOTS_PORT,
          UB_STATIC_DIR: path.join(root, "dist"),
          UB_SKILLS_DIR: path.join(root, "skills"),
          UB_DATA_DIR: dataDir,
          UB_FRAME_ANCESTORS: botsFrameAncestors(),
          UB_DESKTOP_PARENT: "1",
        },
        stdio: ["ignore", "pipe", "pipe"],
      }) as unknown as ChildProcess;
    } catch (err) {
      console.log(`[bots] spawn failed: ${String(err)}`);
      return;
    }
    installBotsOwnerHeader(session.defaultSession);
    installBotsOwnerHeader(session.fromPartition(BOTS_VIEW_PARTITION));
    // The harness learns the owner capability over the parent message port.
    try {
      (
        botsProc as unknown as { postMessage(message: unknown): void }
      ).postMessage({
        type: "universebots:desktop-mutation-token",
        token: BOTS_OWNER_TOKEN,
      });
    } catch (err) {
      console.log(`[bots] could not hand over the owner token: ${String(err)}`);
    }
    void welcomeUniverseBots();
    botsProc.stdout?.on("data", (d: Buffer) => {
      const line = d.toString().trim();
      if (line) console.log(`[bots] ${line}`);
      // The harness falls back to 18799/28799 when 8799 is held. Adopt the
      // port it actually bound so the view, the CSP and the bridge all follow
      // one origin instead of talking to a stale one.
      const bound = line.match(/universebots server on http:\/\/127\.0\.0\.1:(\d+)/);
      if (bound && bound[1] !== BOTS_PORT) {
        const previous = BOTS_PORT;
        BOTS_PORT = bound[1];
        BOTS_ORIGIN = `http://127.0.0.1:${BOTS_PORT}`;
        console.log(`[bots] port changed ${previous} -> ${BOTS_PORT}`);
        installBotsOwnerHeader(session.defaultSession);
        installBotsOwnerHeader(session.fromPartition(BOTS_VIEW_PARTITION));
        botsViewLoaded = false;
        if (botsView && !botsView.webContents.isDestroyed())
          void botsView.webContents.loadURL(BOTS_ORIGIN).catch(() => undefined);
        if (!appQuitting && bridgeStatus === "up") {
          stopBridge();
          bridgeAttempts = 0;
          startBridge();
        }
      }
    });
    botsProc.stderr?.on("data", (d: Buffer) => {
      const line = d.toString().trim();
      if (line) console.log(`[bots:err] ${line}`);
    });
    botsProc.on("error", (err: Error) => {
      botsProc = null;
      console.log(`[bots] spawn error: ${err.message}`);
    });
    botsProc.on("exit", code => {
      botsProc = null;
      if (appQuitting) return;
      if (botsAttempts >= MAX_BOTS_ATTEMPTS) {
        console.log(`[bots] exited (code ${code}) after ${botsAttempts} attempts`);
        return;
      }
      // Another OpenMausBot on this computer may already hold the data
      // directory's lease, in which case the harness refuses to start and
      // restarting it would only spin.
      const delay = Math.min(500 * 2 ** (botsAttempts - 1), 10_000);
      console.log(`[bots] exited code=${code}; restart #${botsAttempts} in ${delay}ms`);
      setTimeout(() => {
        if (!appQuitting) startUniverseBots();
      }, delay);
    });
  }

  function stopUniverseBots(): void {
    const proc = botsProc;
    botsProc = null;
    if (!proc || proc.exitCode !== null || proc.signalCode !== null) return;
    try {
      if (process.platform === "win32" && proc.pid) {
        spawnSync("taskkill", ["/pid", String(proc.pid), "/T", "/F"], {
          stdio: "ignore",
          windowsHide: true,
        });
      } else {
        proc.kill("SIGTERM");
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

  // The bots app is hosted as a real Electron WebContentsView, not a DOM
  // frame. A WebContentsView keeps its own renderer, its own preload and its
  // own IPC registry, which is what window.ogb needs. It is a native view
  // above the page, so a second transparent view carries the surface switcher
  // over it.
  let botsView: WebContentsView | null = null;
  let botsViewLoaded = false;
  let botsSwitcherView: WebContentsView | null = null;
  const BOTS_VIEW_PARTITION = "persist:universebots-embed";
  const TOP_BAR_H = 56;

  function botsPreloadPath(): string {
    return path.join(resolveRepoRoot(), "universe-bots", "electron", "preload.cjs");
  }

  function layoutBotsViews(): void {
    const view = botsView;
    const bar = botsSwitcherView;
    const win = mainWindow;
    if (!win || win.isDestroyed()) return;
    const [width, height] = win.getContentSize();
    const bodyHeight = Math.max(0, height - TOP_BAR_H);
    if (view) view.setBounds({ x: 0, y: TOP_BAR_H, width: Math.max(0, width), height: bodyHeight });
    if (bar) bar.setBounds({ x: 10, y: TOP_BAR_H + 10, width: 248, height: 40 });
  }

  function disposeBotsViews(): void {
    const views = [botsSwitcherView, botsView];
    botsView = null;
    botsViewLoaded = false;
    botsSwitcherView = null;
    for (const view of views) {
      if (!view) continue;
      try {
        if (mainWindow && !mainWindow.isDestroyed())
          mainWindow.contentView.removeChildView(view);
      } catch { /* already detached */ }
      try { view.webContents.close(); } catch { /* already closed */ }
    }
  }

  function createBotsViews(): void {
    if (botsView || !mainWindow || mainWindow.isDestroyed()) return;
    const preload = botsPreloadPath();
    if (!fs.existsSync(preload)) {
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
          partition: BOTS_VIEW_PARTITION,
        },
      });
      botsView = view;
      const handled = installBotsIpc(view.webContents.ipc, () => mainWindow);
      view.webContents.ipc.on("universe:create-bot-via-chat", event => {
        if (event.sender !== view.webContents) return;
        mainWindow?.webContents.send("universe:create-bot-via-chat");
      });
      console.log(`[bots] registered ${handled} IPC channels on the view`);
      // Added last so it paints above the page. The switcher below is added
      // after it and therefore paints on top.
      mainWindow.contentView.addChildView(view);
      layoutBotsViews();
      view.setVisible(false);
      view.webContents.on("did-fail-load", (_e, code, desc) => {
        console.log(`[bots] view load failed: ${code} ${desc}`);
      });
      view.webContents.on("console-message", (_e, level, message) => {
        if (level >= 2) console.log(`[bots:view] ${message}`);
      });
      void view.webContents.loadURL(BOTS_ORIGIN).catch(err => {
        console.log(`[bots] view load error: ${String(err)}`);
      });

      const bar = new WebContentsView({
        webPreferences: {
          preload: path.join(__dirname, "bots-switcher.cjs"),
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
          transparent: true,
        },
      });
      botsSwitcherView = bar;
      mainWindow.contentView.addChildView(bar);
      layoutBotsViews();
      bar.setVisible(false);
      void bar.webContents.loadURL(
        "data:text/html;charset=utf-8," + encodeURIComponent(BOTS_SWITCHER_HTML));
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

  function applyCsp(): void {
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
      // The universe bots app keeps its OWN policy. This handler rewrites
      // every response the session sees, and the framed app's answers come
      // through here too: replacing its policy with the Universe's would
      // block its own scripts and its analytics.
      try {
        const target = new URL(details.url);
        if (
          target.hostname === "127.0.0.1" &&
          Number(target.port || 80) === Number(BOTS_PORT)
        ) {
          callback({ responseHeaders: details.responseHeaders });
          return;
        }
      } catch {
        /* not a URL we can place */
      }
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
              // The bots surface frames the universe bots app, which runs on
              // its own loopback port. Without this the frame inherits
              // default-src 'self' and is blocked before it loads.
              `frame-src 'self' http://127.0.0.1:${BOTS_PORT}`,
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
    // The window opens as wide as the screen allows: the chat column is a
    // MAX-width (1392px), so a narrow window clamps it and the widening
    // becomes invisible (user order, 2026-11-12: "I don't see the chat
    // getting wider"). The work area is what the user can actually use.
    let winW = 1440;
    let winH = 900;
    try {
      const area = screen.getPrimaryDisplay().workAreaSize;
      winW = Math.max(1440, Math.min(area.width - 16, 1800));
      winH = Math.max(780, Math.min(area.height - 16, 1000));
    } catch {
      /* keep the defaults */
    }
    mainWindow = new BrowserWindow({
      width: winW,
      height: winH,
      minWidth: 1024,
      minHeight: 700,
      backgroundColor: "#161616", // Universe canvas tone (docs/shell-tokens.md)
      show: false, // avoid the white flash; shown on ready-to-show
      title: "Universe Agent",
      autoHideMenuBar: true,
      // Universe chrome (Phase 2): the app draws its own title bar
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

    // The bots views are native children: CSS cannot size them, so the host
    // re-lays them out whenever the window changes size.
    mainWindow.on("resize", () => layoutBotsViews());
    createBotsViews();

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
      disposeBotsViews();
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

  // The Bots surface asks main to show/hide its native view and reports the
  // DOMRect of its host so the view lines up with the page, including the
  // custom title bar. Both calls are restricted to the main window.
  ipcMain.on("universe:bots-view-visible", (event, visible: unknown) => {
    if (BrowserWindow.fromWebContents(event.sender) !== mainWindow) return;
    const on = visible === true;
    botsView?.setVisible(on);
    botsSwitcherView?.setVisible(on);
    if (on) layoutBotsViews();
  });

  ipcMain.on(
    "universe:bots-view-bounds",
    (
      event,
      bounds: { x: number; y: number; width: number; height: number },
    ) => {
      if (BrowserWindow.fromWebContents(event.sender) !== mainWindow) return;
      const view = botsView;
      const win = mainWindow;
      if (!view || !win || win.isDestroyed()) return;
      if (!bounds || typeof bounds.width !== "number" || typeof bounds.height !== "number") return;
      const [contentWidth, contentHeight] = win.getContentSize();
      view.setBounds({
        x: Math.max(0, Math.min(contentWidth, Math.round(bounds.x))),
        y: Math.max(0, Math.min(contentHeight, Math.round(bounds.y))),
        width: Math.max(0, Math.min(contentWidth, Math.round(bounds.width))),
        height: Math.max(0, Math.min(contentHeight, Math.round(bounds.height))),
      });
      if (botsSwitcherView)
        botsSwitcherView.setBounds({ x: 10, y: Math.round(bounds.y) + 10, width: 248, height: 40 });
    },
  );

  // The transparent switcher view is a separate renderer: one explicit IPC
  // from it, forwarded to the page that owns wouter navigation.
  ipcMain.on("universe:bots-switch", (event, surface: unknown) => {
    if (event.sender !== botsSwitcherView?.webContents) return;
    if (surface !== "universe" && surface !== "studio" && surface !== "bots") return;
    mainWindow?.webContents.send("universe:navigate-surface", surface);
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
    startUniverseBots();
    healthTimer = setInterval(pollHealth, HEALTH_POLL_MS);
    void createWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) void createWindow();
    });
  });

  app.on("before-quit", () => {
    appQuitting = true;
    if (healthTimer) clearInterval(healthTimer);
    disposeBotsViews();
    stopBridge();
    stopUniverseBots();
  });

  app.on("window-all-closed", () => {
    appQuitting = true;
    disposeBotsViews();
    stopBridge();
    stopUniverseBots();
    if (process.platform !== "darwin") app.quit();
  });
}
