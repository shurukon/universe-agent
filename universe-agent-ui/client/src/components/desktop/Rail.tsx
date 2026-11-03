/**
 * components/desktop/Rail.tsx — the right rail: Preview · Browser · Files ·
 * Progress · Terminal.
 *
 *   Preview (M2, functional) — the unified in-app preview surface, fed by
 *             GET /api/file: text/code (mono block), markdown (Streamdown),
 *             images, audio, video (Range-seekable), PDF (native viewer),
 *             HTML (sandboxed srcdoc — the bridge token never reaches an
 *             agent-generated page). Zero popups.
 *   Browser  — the live screencast pane (M4 placeholder) + a live badge
 *             while the browser agent is driving.
 *   Files    — artifacts of recent finished goals: a click previews, the
 *             ↗ button opens with the system handler.
 *   Terminal — the user's one-shot shell over the /api/terminal WS (no
 *             PTY, Phase C): every line is a full run, `cd` rides the
 *             explicit cwd protocol so it sticks between lines.
 *
 * Zero popups: everything renders inside this panel, nothing ever spawns a
 * window.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Streamdown } from "streamdown";
import {
  Activity,
  Eye,
  Globe,
  Folder,
  X,
  RotateCcw,
  FileText,
  FileDiff,
  ExternalLink,
  ListChecks,
  MessageSquare,
  Terminal,
} from "lucide-react";
import { api, BridgeError, bridgeConfig } from "@/lib/api";
import { useChat } from "@/stores/chat";
import { useLiveGoalId } from "@/hooks/useLiveGoalId";
import { useRail } from "@/lib/flow/railStore";
import ActivityPane from "./ActivityPane";
import ProgressPane from "./ProgressPane";


interface ArtifactRow {
  turn_id: string;
  label: string;
  bytes: number | null;
}

type UniversePreload = {
  openPath?: (p: string) => void;
};

/* ------------------------------------------------------------------ */
/* Side conversation pane — the steered follow-ups of this task        */
/* ------------------------------------------------------------------ */

function SideChatPane() {
  const turnId = useLiveGoalId();
  const steered = useChat(s =>
    turnId
      ? s.messages.filter(
          m => m.role === "user" && m.turn_id === turnId && m.steered
        )
      : []
  );

  return (
    <div className="rail-pane">
      <div className="rail-pane-head">
        <span className="rail-pane-title">Side conversation</span>
      </div>
      {steered.length === 0 && (
        <div className="rail-note">
          No side turns yet — a short message typed while the task runs steers
          it without ending the goal.
        </div>
      )}
      {steered.map(m => (
        <div key={m.id} className="rail-note side-turn">
          {m.text}
        </div>
      ))}
      <div className="rail-note dim">
        Type follow-ups in the main composer — Enter steers the running task,
        Esc stops it.
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Files pane — artifacts of recent finished goals (functional today)  */
/* ------------------------------------------------------------------ */

function FilesPane({ title }: { title?: string }) {
  const [rows, setRows] = useState<ArtifactRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    setErr(null);
    try {
      const { goals } = await api.goals(10);
      const done = goals.filter(
        g => g.status !== "running" && g.artifacts && g.artifacts.length
      );
      const out: ArtifactRow[] = [];
      for (const g of done.slice(0, 10)) {
        for (const a of g.artifacts ?? []) {
          const label = String(
            (a as { path?: string; label?: string }).path ??
              (a as { label?: string }).label ??
              ""
          );
          if (!label) continue;
          out.push({
            turn_id: g.turn_id,
            label,
            bytes:
              typeof (a as { bytes?: unknown }).bytes === "number"
                ? (a as { bytes: number }).bytes
                : null,
          });
        }
        if (out.length >= 40) break;
      }
      setRows(out);
    } catch (e) {
      setErr(
        e instanceof BridgeError ? e.message : "Could not list recent goals"
      );
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openExternal = (path: string) => {
    const preload = (window as unknown as { universe?: UniversePreload })
      .universe;
    if (preload?.openPath) preload.openPath(path);
  };

  return (
    <div className="rail-pane">
      <div className="rail-pane-head">
        <span className="rail-pane-title mono">
          {title ?? "artifacts · recent finished goals"}
        </span>
        <button
          type="button"
          className="rail-icon-btn"
          onClick={() => void load()}
          aria-label="Refresh artifacts"
          title="Refresh"
          disabled={refreshing}
        >
          <RotateCcw size={12} strokeWidth={2} />
        </button>
      </div>
      {err && <div className="rail-note err">{err}</div>}
      {rows === null && !err && <div className="rail-note">loading…</div>}
      {rows !== null && rows.length === 0 && (
        <div className="rail-note">
          No artifacts yet — files the agents produce (documents, images,
          reports) land here.
        </div>
      )}
      {rows?.map((r, i) => (
        <div key={`${r.turn_id}:${i}`} className="rail-file-row">
          <button
            type="button"
            className="rail-file"
            onClick={() => useRail.getState().openPreview(r.label)}
            title={`Preview ${r.label}`}
          >
            <FileText size={12} strokeWidth={1.8} aria-hidden="true" />
            <span className="rail-file-name mono">{r.label}</span>
            {r.bytes !== null && (
              <span className="rail-file-size mono num">
                {r.bytes >= 1024
                  ? `${(r.bytes / 1024).toFixed(1)} KB`
                  : `${r.bytes} B`}
              </span>
            )}
          </button>
          <button
            type="button"
            className="rail-icon-btn"
            onClick={() => openExternal(r.label)}
            aria-label="Open externally"
            title="Open externally"
          >
            <ExternalLink size={12} strokeWidth={2} />
          </button>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Preview pane — the unified preview surface (M2)                     */
/* ------------------------------------------------------------------ */

function extOf(path: string): string {
  const base = path.split(/[\\/]/).pop() ?? path;
  const dot = base.lastIndexOf(".");
  return dot >= 0 ? base.slice(dot + 1).toLowerCase() : "";
}

const MD_EXTS = new Set(["md", "markdown"]);
const TEXT_EXTS = new Set([
  "txt",
  "log",
  "json",
  "csv",
  "tsv",
  "py",
  "ts",
  "tsx",
  "js",
  "jsx",
  "css",
  "scss",
  "xml",
  "yml",
  "yaml",
  "toml",
  "ini",
  "cfg",
  "conf",
  "sh",
  "bat",
  "ps1",
  "Sql",
  "rs",
  "go",
  "java",
  "c",
  "h",
  "cpp",
  "hpp",
  "cs",
  "rb",
  "php",
  "swift",
  "kt",
  "lua",
  "r",
  "env",
  "diff",
  "patch",
  "gitignore",
  "dockerfile",
  "editorconfig",
]);
const IMG_EXTS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "svg",
  "bmp",
  "ico",
  "avif",
]);
const AUDIO_EXTS = new Set([
  "mp3",
  "wav",
  "ogg",
  "oga",
  "m4a",
  "flac",
  "opus",
  "aac",
]);
const VIDEO_EXTS = new Set(["mp4", "webm", "mov", "mkv", "avi", "m4v"]);

type PreviewKind =
  "md" | "text" | "image" | "audio" | "video" | "Pdf" | "html" | "binary";

function previewKind(path: string): PreviewKind {
  const e = extOf(path);
  if (MD_EXTS.has(e)) return "md";
  if (IMG_EXTS.has(e)) return "image";
  if (AUDIO_EXTS.has(e)) return "audio";
  if (VIDEO_EXTS.has(e)) return "video";
  if (e === "Pdf") return "Pdf";
  if (e === "html" || e === "htm") return "html";
  if (TEXT_EXTS.has(e)) return "text";
  return "binary";
}

function PreviewPane() {
  const previewPath = useRail(s => s.previewPath);
  const setTab = useRail(s => s.setTab);
  const [url, setUrl] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setUrl(null);
    setText(null);
    setErr(null);
    if (!previewPath) return;
    setLoading(true);
    void (async () => {
      try {
        const u = await api.fileUrl(previewPath);
        if (cancelled) return;
        setUrl(u);
        const kind = previewKind(previewPath);
        if (kind === "md" || kind === "text" || kind === "html") {
          const res = await fetch(u);
          if (!res.ok) throw new Error(`preview failed (${res.status})`);
          const t = await res.text();
          if (!cancelled) setText(t.slice(0, 200_000));
        }
      } catch (e) {
        if (!cancelled)
          setErr(e instanceof Error ? e.message : "preview failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [previewPath]);

  if (!previewPath) {
    return (
      <div className="rail-pane">
        <div className="rail-note">
          Nothing to preview yet — click an artifact chip in the chat flow or a
          file in the Files tab.
        </div>
      </div>
    );
  }

  const name = previewPath.split(/[\\/]/).pop() ?? previewPath;
  const kind = previewKind(previewPath);
  const openExternal = () => {
    const preload = (window as unknown as { universe?: UniversePreload })
      .universe;
    if (preload?.openPath) preload.openPath(previewPath);
  };

  return (
    <div className="rail-pane">
      <div className="rail-pane-head">
        <span className="rail-pane-title mono" title={previewPath}>
          {name}
        </span>
        <span className="rail-head-actions">
          <button
            type="button"
            className="sx-btn"
            style={{ fontSize: 11, padding: "2px 6px" }}
            onClick={async () => {
              try {
                const u = await api.fileUrl(previewPath);
                await api.navigateBrowser(u);
                useRail.getState().setTab("browser");
              } catch {}
            }}
            title="Open in Browser"
          >
            Browser
          </button>
          <button
            type="button"
            className="rail-icon-btn"
            onClick={openExternal}
            aria-label="Open with system handler"
            title="Open externally"
          >
            <ExternalLink size={12} strokeWidth={2} />
          </button>
          <button
            type="button"
            className="rail-icon-btn"
            onClick={() => setTab("files")}
            aria-label="Close preview"
            title="Close preview"
          >
            <X size={12} strokeWidth={2} />
          </button>
        </span>
      </div>

      {loading && <div className="rail-note dim">loading…</div>}
      {err && <div className="rail-note err">{err}</div>}

      {!err && kind === "md" && text && (
        <Streamdown mode="static" className="pv-md ar-md">
          {text}
        </Streamdown>
      )}
      {!err && kind === "text" && text && (
        <pre className="pv-code mono">{text}</pre>
      )}
      {!err && kind === "html" && (
        <iframe
          className="pv-frame"
          title={`${name} preview`}
          sandbox="allow-scripts"
          srcDoc={text ?? ""}
        />
      )}
      {!err && kind === "image" && url && (
        <img className="pv-img" src={url} alt={name} />
      )}
      {!err && kind === "audio" && url && (
        <audio className="pv-audio" controls preload="metadata" src={url} />
      )}
      {!err && kind === "video" && url && (
        <video className="pv-video" controls preload="metadata" src={url} />
      )}
      {!err && kind === "Pdf" && url && (
        <iframe className="pv-frame" title={`${name} preview`} src={url} />
      )}
      {!err && kind === "binary" && (
        <div className="rail-note">
          Binary file — no inline preview for this type yet. Open it externally
          instead.
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Browser pane — the LIVE view (M4): CDP screencast frames over the    */
/* dedicated /api/browser/stream WebSocket. Lazy: connecting here is    */
/* what starts the agent's browser streaming; closing the pane stops    */
/* it. Zero popups — the agent's Chromium renders inside this panel.    */
/* ------------------------------------------------------------------ */

function BrowserPane({ browserActive }: { browserActive: boolean }) {
  const [status, setStatus] = useState<
    "connecting" | "live" | "retrying" | "error"
  >("connecting");
  const [url, setUrl] = useState("");
  const [urlInput, setUrlInput] = useState("");
  const [errText, setErrText] = useState<string | null>(null);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const imgRef = useRef<string | null>(null);

  useEffect(() => {
    let closed = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = async () => {
      if (closed) return;
      try {
        const { url: base, token } = await bridgeConfig();
        const wsBase = base.replace(/^http/, "ws");
        const socket = new WebSocket(
          `${wsBase}/api/browser/stream${
            token ? `?token=${encodeURIComponent(token)}` : ""
          }`
        );
        ws = socket;
        socket.binaryType = "arraybuffer";
        socket.onmessage = ev => {
          if (typeof ev.data === "string") {
            try {
              const m = JSON.parse(ev.data) as {
                type?: string;
                url?: string;
                error?: string;
              };
              if (m.type === "browser_meta" && typeof m.url === "string")
                setUrl(m.url);
              if (m.type === "browser_error") {
                setErrText(m.error ?? "browser unavailable");
                setStatus("error");
              }
            } catch {
              /* malformed meta — the frames are the payload that matters */
            }
            return;
          }
          const blobUrl = URL.createObjectURL(
            new Blob([ev.data], { type: "image/jpeg" })
          );
          if (imgRef.current) URL.revokeObjectURL(imgRef.current);
          imgRef.current = blobUrl;
          setImgUrl(blobUrl);
          setStatus("live");
        };
        socket.onclose = () => {
          if (ws === socket) ws = null;
          if (!closed) {
            setStatus("retrying");
            retryTimer = setTimeout(() => void connect(), 3000);
          }
        };
        socket.onerror = () => socket.close();
      } catch {
        setStatus("error");
      }
    };

    void connect();
    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
      if (imgRef.current) {
        URL.revokeObjectURL(imgRef.current);
        imgRef.current = null;
      }
    };
  }, []);

  React.useEffect(() => {
    if (url) setUrlInput(url);
  }, [url]);
  return (
    <div className="rail-pane">
      <div className="rail-pane-head">
        <span className="rail-pane-title mono" title={url}>
          {url || "browser"}
        </span>
        {browserActive && (
          <span
            className="rail-live-dot"
            role="status"
            aria-label="the browser agent is driving"
          />
        )}
      </div>
      <form
        className="browser-bar"
        style={{ display: "flex", gap: 6, padding: "6px 8px" }}
        onSubmit={async e => {
          e.preventDefault();
          const u = urlInput.trim();
          if (!u) return;
          try {
            const { api } = await import("@/lib/api");
            await api.navigateBrowser(u);
            setUrl(u);
          } catch {}
        }}
      >
        <input
          className="sx-input"
          value={urlInput}
          onChange={e => setUrlInput((e.target as HTMLInputElement).value)}
          placeholder="https://example.com"
          style={{ flex: 1 }}
        />
        <button type="submit" className="sx-btn">
          Go
        </button>
      </form>
      {imgUrl ? (
        <img
          className="pv-frame-img"
          src={imgUrl}
          alt="live browser view"
          draggable={false}
        />
      ) : (
        <div className="rail-note dim">
          {status === "error"
            ? (errText ?? "browser unavailable")
            : status === "retrying"
              ? "connection lost — retrying…"
              : "connecting to the browser agent's Chromium…"}
        </div>
      )}
      <div className="rail-note dim">
        Live view of the browser agent's active tab — it starts streaming when
        the browser runs; the agent's actions also stream as tool rows in the
        chat flow.
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Terminal pane — the user's one-shot shell over /api/terminal (WS)  */
/* ------------------------------------------------------------------ */

type TermLine =
  | { kind: "cmd"; text: string }
  | { kind: "out"; text: string }
  | { kind: "err"; text: string }
  | { kind: "sys"; text: string };

function TerminalPane() {
  const [lines, setLines] = useState<TermLine[]>([]);
  const [cwd, setCwd] = useState("");
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const historyRef = useRef<string[]>([]);
  const histIdx = useRef(-1);

  const push = useCallback((...ls: TermLine[]) => {
    setLines(prev => [...prev, ...ls]);
  }, []);

  // one socket per mounted pane; closed on unmount (tab switch is fine —
  // the pane remounts and reconnects, cwd state lives server-side per
  // connection, so a fresh socket starts back at the project root)
  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    void api.terminalSocketUrl().then(url => {
      if (disposed) return;
      ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onmessage = ev => {
        let msg: {
          type: string;
          cwd?: string;
          cmd?: string;
          stdout?: string;
          stderr?: string;
          returncode?: number | null;
          elapsed_ms?: number;
          message?: string;
        };
        try {
          msg = JSON.parse(ev.data as string);
        } catch {
          return;
        }
        if (msg.type === "ready") {
          setCwd(msg.cwd || "");
          push({
            kind: "sys",
            text: "terminal ready — every line is a full shell run",
          });
        } else if (msg.type === "cwd") {
          setCwd(msg.cwd || "");
        } else if (msg.type === "start") {
          push({ kind: "cmd", text: `${cwd}> ${msg.cmd ?? ""}` });
        } else if (msg.type === "result") {
          if (msg.stdout)
            push({
              kind: "out",
              text: msg.stdout.replace(/\r\n/g, "\n").replace(/\n$/, ""),
            });
          if (msg.stderr)
            push({
              kind: "err",
              text: msg.stderr.replace(/\r\n/g, "\n").replace(/\n$/, ""),
            });
          if ((msg.returncode ?? 0) !== 0)
            push({
              kind: "sys",
              text: `exit code ${msg.returncode} (${msg.elapsed_ms ?? 0}ms)`,
            });
          setRunning(false);
        } else if (msg.type === "error") {
          push({ kind: "err", text: msg.message || "unknown terminal error" });
          setRunning(false);
        }
      };
      ws.onclose = () => {
        if (!disposed)
          push({
            kind: "sys",
            text: "terminal connection closed — reopening the tab reconnects",
          });
      };
    });
    return () => {
      disposed = true;
      ws?.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // autoscroll on new output
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [lines]);

  const submit = () => {
    const ws = wsRef.current;
    const text = input.trim();
    if (!ws || ws.readyState !== WebSocket.OPEN || !text || running) return;
    historyRef.current.unshift(text);
    histIdx.current = -1;
    setInput("");
    // a bare `cd …` rides the explicit cwd protocol so it sticks between
    // runs; anything else (including `cd x && …` compounds) is a run
    const cd = /^cd(?:\s+(.*))?$/.exec(text);
    if (cd) {
      ws.send(
        JSON.stringify({ type: "cd", path: (cd[1] ?? "").trim() || "~" })
      );
    } else {
      setRunning(true);
      ws.send(JSON.stringify({ type: "run", cmd: text }));
    }
  };

  const onInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const h = historyRef.current;
      if (h.length === 0) return;
      histIdx.current = Math.min(histIdx.current + 1, h.length - 1);
      setInput(h[histIdx.current]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      const h = historyRef.current;
      if (histIdx.current <= 0) {
        histIdx.current = -1;
        setInput("");
      } else {
        histIdx.current -= 1;
        setInput(h[histIdx.current]);
      }
    }
  };

  const shortCwd = cwd ? (cwd.length > 34 ? `…${cwd.slice(-33)}` : cwd) : "";

  return (
    <div className="term-pane">
      <div className="term-scroll" ref={scrollRef} aria-live="polite">
        {lines.map((l, i) => (
          <pre key={i} className={`term-line term-${l.kind}`}>
            {l.text}
          </pre>
        ))}
        {running && <pre className="term-line term-sys">running…</pre>}
      </div>
      <div className="term-inputrow">
        <span className="term-prompt mono" title={cwd}>
          {shortCwd}
          {"\u003e"}
        </span>
        <input
          ref={inputRef}
          className="term-input mono"
          value={input}
          spellCheck={false}
          autoComplete="off"
          aria-label="Terminal command"
          placeholder={
            running ? "waiting for the command to finish…" : "type a command"
          }
          disabled={running}
          onChange={e => setInput(e.target.value)}
          onKeyDown={onInputKeyDown}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the rail                                                            */
/* ------------------------------------------------------------------ */

const TABS: Array<{
  id: import("@/lib/flow/railStore").RailTab;
  label: string;
  icon: typeof Eye;
  desc: string;
  chip?: "main" | "extra";
}> = [
  // the Universe side-pane tabs first — pick order matches its empty state
  {
    id: "selectionchat",
    label: "Side conversation",
    icon: MessageSquare,
    desc: "Quick follow-ups while the task runs",
    chip: "main",
  },
  {
    id: "review",
    label: "Review",
    icon: FileDiff,
    desc: "Review the task's products",
    chip: "main",
  },
  {
    id: "terminal",
    label: "Terminal",
    icon: Terminal,
    desc: "A one-shot shell at the project root",
    chip: "main",
  },
  {
    id: "browser",
    label: "Browser",
    icon: Globe,
    desc: "The browser agent's Chromium, live",
    chip: "main",
  },
  // Universe workspace tools — same panes, grouped below the Universe four
  {
    id: "activity",
    label: "Activity",
    icon: Activity,
    desc: "The live agent tree while a goal runs",
    chip: "extra",
  },
  {
    id: "preview",
    label: "Preview",
    icon: Eye,
    desc: "Documents, images, code — zero popups",
    chip: "extra",
  },
  {
    id: "files",
    label: "Files",
    icon: Folder,
    desc: "Artifacts of recent finished goals",
    chip: "extra",
  },
  {
    id: "progress",
    label: "Progress",
    icon: ListChecks,
    desc: "The run's todo score at a glance",
    chip: "extra",
  },
];

const MAIN_TABS = TABS.filter(t => t.chip === "main");
const EXTRA_TABS = TABS.filter(t => t.chip === "extra");

export default function Rail({
  browserActive,
  onClose,
}: {
  /** true while the running goal's stream carries browser tool calls */
  browserActive: boolean;
  onClose: () => void;
}) {
  const tab = useRail(s => s.tab);
  const setTab = useRail(s => s.setTab);
  const capsule = useRail(s => s.capsule);
  const toggleCapsule = useRail(s => s.toggleCapsule);
  // drag-resize width (320 → 560), pointer-events only on the handle
  // shell parity: fixed-width rail (336px via CSS), no drag-resize

  // drag-resize removed: Universe uses a fixed-width rail

  // collapse-to-capsule (Universe Progress): the rail becomes a small pill
  // with the live todo score; one click brings the full pane back
  if (capsule) {
    return (
      <aside className="rail rail-capsule-mode">
        <button
          type="button"
          className="rail-capsule"
          onClick={toggleCapsule}
          title="Expand the panel"
          aria-label="Expand panel"
        >
          <ListChecks size={12} strokeWidth={1.8} aria-hidden="true" />
          <ProgressScore />
        </button>
      </aside>
    );
  }

  // Universe bar anatomy: icon pills — every tab is an icon; the ACTIVE one
  // expands to its label. Five pills + the capsule/close pair never
  // overflow the 336px rail.
  const renderTabs = () => (
    <>
      {MAIN_TABS.map(t => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={tab === t.id}
          aria-label={t.label}
          title={t.label}
          className={`rail-tab${tab === t.id ? " active" : ""}`}
          onClick={() => setTab(t.id)}
        >
          <t.icon size={12} strokeWidth={1.8} aria-hidden="true" />
          {tab === t.id && <span className="rail-tab-label">{t.label}</span>}
          {t.id === "browser" && browserActive && (
            <span className="rail-live-dot" aria-label="browser agent active" />
          )}
        </button>
      ))}
      {EXTRA_TABS.filter(t => t.id === tab).map(t => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={true}
          className="rail-tab active"
          title={t.label}
          onClick={() => setTab(t.id)}
        >
          <t.icon size={12} strokeWidth={1.8} aria-hidden="true" />
          <span className="rail-tab-label">{t.label}</span>
        </button>
      ))}
    </>
  );

  return (
    <aside className="rail">
      {/* StatusCard removed per user annotation — "Agents 1 running" duplicate deleted */}
      {tab === "open" ? (
        <div className="rail-pane rail-open">
          <div className="rail-open-head">
            <b>Open tab</b>
            <span>Choose a tab to open in the side pane</span>
          </div>
          <div className="rail-open-cards" role="list">
            {MAIN_TABS.map(t => (
              <button
                key={t.id}
                type="button"
                className="rail-open-card tall"
                role="listitem"
                onClick={() => setTab(t.id)}
              >
                <span className="oc-ic" aria-hidden="true">
                  <t.icon size={16} strokeWidth={1.8} />
                </span>
                <b>{t.label}</b>
              </button>
            ))}
          </div>
          {/* Universe-only panes — Universe's 3.12.1 picker shows just the four
              main tabs, so the extras stay reachable as one quiet chip row */}
          <div className="rail-open-more" role="list">
            {EXTRA_TABS.map(t => (
              <button
                key={t.id}
                type="button"
                role="listitem"
                title={t.desc}
                onClick={() => setTab(t.id)}
              >
                <t.icon size={12} strokeWidth={1.8} />
                {t.label}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <>
          <div className="rail-tabs" role="tablist">
            {renderTabs()}
            <button
              type="button"
              className="rail-icon-btn"
              onClick={toggleCapsule}
              aria-label="Collapse to capsule"
              title="Collapse to capsule"
            >
              <ChevronRightRail />
            </button>
            <button
              type="button"
              className="rail-icon-btn close"
              onClick={onClose}
              aria-label="Close panel"
              title="Close"
            >
              <X size={13} strokeWidth={2} />
            </button>
          </div>

          <div className="rail-body">
            {tab === "files" && <FilesPane />}
            {tab === "review" && <FilesPane title="Review" />}
            {tab === "preview" && <PreviewPane />}
            {tab === "browser" && <BrowserPane browserActive={browserActive} />}
            {tab === "progress" && <ProgressPane />}
            {tab === "terminal" && <TerminalPane />}
            {tab === "activity" && <ActivityPane />}
            {tab === "selectionchat" && <SideChatPane />}
          </div>
        </>
      )}
    </aside>
  );
}

/** the capsule's live N/M readout — the pane goal's latest todo_updated */
function ProgressScore() {
  const turnId = useLiveGoalId();
  const events = useChat(s => (turnId ? s.eventsByGoal[turnId] : undefined));
  let done = 0;
  let total = 0;
  if (events) {
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev.type === "todo_updated" && Array.isArray(ev.todos)) {
        total = ev.todos.length;
        done = ev.todos.filter(t => t.status === "completed").length;
        break;
      }
    }
  }
  return <span>{total > 0 ? `${done}/${total}` : "—"}</span>;
}

/** a tiny chevron glyph (kept inline: one icon, no extra import churn) */
function ChevronRightRail() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="m9 18 6-6-6-6"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** true while the goal's stream carries browser tool calls (Chat.tsx uses
 *  this to auto-open the Browser tab at the right moment). */
export function useRailBrowserActive(turnId: string | null): boolean {
  const eventsByGoal = useChat(s => s.eventsByGoal);
  if (!turnId) return false;
  const evs = eventsByGoal[turnId];
  if (!evs || evs.length === 0) return false;
  return evs.some(
    e =>
      typeof e.tool === "string" &&
      e.tool.startsWith("browser_") &&
      (e.type === "tool_running" ||
        e.type === "tool_finished" ||
        e.type === "tool_failed")
  );
}