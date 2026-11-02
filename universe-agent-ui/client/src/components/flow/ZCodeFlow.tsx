/**
 * components/flow/ZCodeFlow.tsx — the ZCode-parity rendering surface
 * (full-parity plan P2). Two exports:
 *
 *   · ZTList — the timeline body: reasoning rows ("Thinking"/"Thought" +
 *     duration), family-folded tool rows (Explore/Terminal/Changes ·
 *     "{count} events"), nested SubAgent rows, compaction separators.
 *   · StatusPanel — the chat.statusPanel.* sections (Goal · Agents ·
 *     Progress · Changes · Terminals · Commit / Push · Git tools ·
 *     Workflows) with the exact i18n strings from ZCode 3.14.3.
 *
 * Styling lives in zcode-flow.css (ZCode measured tokens, docs/zcode-tokens.md).
 */
import { useState, type ReactNode } from "react";
import {
  AppWindow,
  Archive,
  BadgeCheck,
  BookOpen,
  Boxes,
  Braces,
  Brain,
  Bug,
  Calendar,
  CalendarClock,
  CalendarPlus,
  CalendarX2,
  Camera,
  ChartColumn,
  Check,
  ChevronRight,
  CircleCheckBig,
  ClipboardList,
  ClipboardPen,
  ClipboardPlus,
  Cloud,
  CloudSun,
  Coins,
  Container,
  Contrast,
  Copy,
  Database,
  Eye,
  FileClock,
  FileCode,
  FileCode2,
  FileJson,
  FilePenLine,
  FilePlus2,
  FileSearch,
  FileText,
  FlaskConical,
  Folder,
  FolderSearch,
  Gauge,
  GitBranch,
  GitCommitHorizontal,
  GitCompare,
  Github,
  Gitlab,
  Globe,
  History,
  Image as ImageIcon,
  KeyRound,
  Keyboard,
  LayoutGrid,
  Link2,
  ListChecks,
  ListTodo,
  ListTree,
  LoaderCircle,
  MessageCircleQuestion,
  Monitor,
  MonitorDot,
  Mouse,
  MousePointer2,
  MousePointerClick,
  Move,
  Network,
  Notebook,
  OctagonX,
  Package,
  PackageSearch,
  PanelsTopLeft,
  Pause,
  Plug,
  PlugZap,
  ReceiptText,
  Rocket,
  RotateCcw,
  ScanEye,
  ScanText,
  ScrollText,
  Search,
  SearchCode,
  Send,
  Server,
  Shapes,
  ShieldCheck,
  Ship,
  Slack,
  Sparkles,
  SquarePen,
  SquareTerminal,
  Table,
  Target,
  TextCursorInput,
  Timer,
  Trash2,
  Trello,
  TriangleAlert,
  Upload,
  Waypoints,
  Wrench,
  X,
  ZoomIn,
} from "lucide-react";
import type { ZTEntry, ZTModel } from "@/lib/flow/ztimeline";
import {
  fmtZcodeDuration,
  fmtShortDuration,
} from "@/lib/flow/ztimeline";

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

function fmtTokens(n: number | null): string | null {
  if (n === null) return null;
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return String(n);
}

/** total row duration: sum of the folded call durations, or live "since start" */
function rowDurationMs(e: ZTEntry, running: boolean): number | null {
  if (e.kind === "tool") {
    if (e.status === "in_progress") {
      // live: measure from the row's first event to now — only while the
      // goal is still running (static history ends mid-flight)
      return running ? e.durationMs : e.durationMs || null;
    }
    return e.durationMs > 0 ? e.durationMs : null;
  }
  if (e.status === "in_progress") return null;
  return e.durationMs > 0 ? e.durationMs : null;
}

/** ZCode icon fidelity (measured DOM): every row icon is a lucide glyph
 *  at 16px (size-4) with stroke-width 1.5 and the foreground-subtlest
 *  30% ink — never full ink, never 12px. */
const ICO = { size: 16, strokeWidth: 1.5 } as const;

/**
 * Per-tool professional icon map — Apple SF Symbols philosophy:
 * rounded, stroke 1.5, optical compensation. Each tool owns its glyph,
 * never a generic "Explore" bucket. The registry keys are lowercase.
 */
/** Audit hook: exported so the live coverage test can prove every
 *  registered tool maps to a non-generic glyph. */
export function toolIconFor(name: string | null): ReactNode {
  if (!name) return <Wrench {...ICO} />;
  const t = name.toLowerCase().replace(/[._\s-]/g, "");
  /* ---- browser family (specifics BEFORE the generic prefix) ---- */
  if (t === "browserconnect") return <Plug {...ICO} />;
  if (t === "browsernavigate") return <Globe {...ICO} />;
  if (t === "browserclick") return <MousePointerClick {...ICO} />;
  if (t === "browserfill") return <TextCursorInput {...ICO} />;
  if (t === "browserscreenshot") return <Camera {...ICO} />;
  if (t === "browsersnapshot") return <ScanEye {...ICO} />;
  if (t === "browserconsole") return <SquareTerminal {...ICO} />;
  if (t === "browserextract") return <Copy {...ICO} />;
  if (t === "browsertabs") return <PanelsTopLeft {...ICO} />;
  if (t === "browserupload") return <Upload {...ICO} />;
  if (t.startsWith("browser") || t === "controlbrowser" || t === "browseruse")
    return <Globe {...ICO} />;
  /* ---- SaaS integrations (brands lucide ships, else plug semantics) ---- */
  if (t.startsWith("airtable")) return <Database {...ICO} />;
  if (t.startsWith("dropbox") || t.startsWith("googledrive")) return <Cloud {...ICO} />;
  if (t.startsWith("coingecko")) return <Coins {...ICO} />;
  if (t.startsWith("openweathermap")) return <CloudSun {...ICO} />;
  if (t.startsWith("slack")) return <Slack {...ICO} />;
  if (t.startsWith("trello")) return <Trello {...ICO} />;
  if (t.startsWith("github")) return <Github {...ICO} />;
  if (t.startsWith("gitlab")) return <Gitlab {...ICO} />;
  if (t.startsWith("asana") || t.startsWith("jira") || t.startsWith("linear") ||
      t.startsWith("notion") || t.startsWith("hubspot") || t.startsWith("stripe") ||
      t.startsWith("discord") || t.startsWith("telegram") || t.startsWith("shopping")) {
    if (/(create|post|send|webhook)/.test(t)) return <Plug {...ICO} />;
    return <PlugZap {...ICO} />;
  }
  /* ---- git family ---- */
  if (t === "gitcommit") return <GitCommitHorizontal {...ICO} />;
  if (t === "gitdiff") return <GitCompare {...ICO} />;
  if (t === "gitlog") return <History {...ICO} />;
  if (t === "gitpush") return <Upload {...ICO} />;
  if (t.startsWith("git")) return <GitBranch {...ICO} />;
  /* ---- files ---- */
  if (t === "read" || t === "fileread" || t === "readfile" || t === "readtext")
    return <FileText {...ICO} />;
  if (t === "write" || t === "filewrite" || t === "createfile")
    return <FilePlus2 {...ICO} />;
  if (t === "edit" || t === "fileedit" || t === "editfile" || t === "patch" ||
      t === "applypatch")
    return <FilePenLine {...ICO} />;
  if (t === "delete" || t === "filedelete" || t === "deletefile")
    return <Trash2 {...ICO} />;
  if (t === "ls" || t === "filelist" || t === "listfiles") return <Folder {...ICO} />;
  if (t === "glob" || t === "globsearch") return <FolderSearch {...ICO} />;
  if (t === "grep") return <SearchCode {...ICO} />;
  if (t === "notebookedit") return <Notebook {...ICO} />;
  if (t === "archive") return <Archive {...ICO} />;
  if (t === "openapiparser") return <FileJson {...ICO} />;
  if (t === "svggen") return <Shapes {...ICO} />;
  if (t === "readsessioncontext") return <ScrollText {...ICO} />;
  if (t === "codeanalyzer") return <FileSearch {...ICO} />;
  if (t === "staticanalysis") return <FileCode2 {...ICO} />;
  /* ---- web ---- */
  if (t === "websearch") return <Search {...ICO} />;
  if (t === "toolsearch") return <ListTree {...ICO} />;
  if (t === "webfetch" || t === "httpclient" || t === "fetch") return <Link2 {...ICO} />;
  if (t === "webresearch" || t === "wikipediasearch" || t === "arxivsearch")
    return <BookOpen {...ICO} />;
  /* ---- shell / ops ---- */
  if (t === "bash" || t === "bashunsandboxed" || t === "powershell" ||
      t === "codeexecute" || t.startsWith("shell") || t === "terminal")
    return <SquareTerminal {...ICO} />;
  if (t === "ssh") return <Server {...ICO} />;
  if (t === "logtail") return <FileClock {...ICO} />;
  /* ---- schedules ---- */
  if (t === "croncreate") return <CalendarPlus {...ICO} />;
  if (t === "cronlist") return <Calendar {...ICO} />;
  if (t === "cronupdate") return <CalendarClock {...ICO} />;
  if (t === "crondelete") return <CalendarX2 {...ICO} />;
  /* ---- node REPL family ---- */
  if (t === "js") return <SquareTerminal {...ICO} />;
  if (t === "jsreset") return <RotateCcw {...ICO} />;
  if (t === "jsaddnodemoduledir") return <Package {...ICO} />;
  /* ---- plan / task ledger ---- */
  if (t === "enterplanmode") return <SquarePen {...ICO} />;
  if (t === "exitplanmode") return <CircleCheckBig {...ICO} />;
  if (t === "capabilitycatalog") return <ListChecks {...ICO} />;
  if (t === "taskcreate") return <ClipboardPlus {...ICO} />;
  if (t === "taskupdate") return <ClipboardPen {...ICO} />;
  if (t === "taskget") return <ClipboardList {...ICO} />;
  if (t === "tasklist") return <PanelsTopLeft {...ICO} />;
  if (t === "taskstop") return <OctagonX {...ICO} />;
  if (t === "taskoutput") return <ReceiptText {...ICO} />;
  if (t === "todoread" || t === "todowrite") return <ListTodo {...ICO} />;
  if (t === "goalread") return <Target {...ICO} />;
  if (t === "askuserquestion" || t === "askuser" || t === "question")
    return <MessageCircleQuestion {...ICO} />;
  if (t === "monitor") return <Timer {...ICO} />;
  if (t === "sendmessage") return <Send {...ICO} />;
  if (t.startsWith("skill")) return <Sparkles {...ICO} />;
  /* ---- data / quality / infra ---- */
  if (t === "memoryquery" || t === "memory" || t === "recall") return <Brain {...ICO} />;
  if (t === "pandasquery") return <Table {...ICO} />;
  if (t === "datavalidate") return <BadgeCheck {...ICO} />;
  if (t === "charting") return <ChartColumn {...ICO} />;
  if (t === "coveragereport") return <Gauge {...ICO} />;
  if (t === "testrunner") return <FlaskConical {...ICO} />;
  if (t === "dependencyaudit") return <PackageSearch {...ICO} />;
  if (t === "pypipackageinfo" || t === "npmpackageinfo" || t === "cratespackageinfo")
    return <Package {...ICO} />;
  if (t === "secretscanner") return <KeyRound {...ICO} />;
  if (t === "vulnscanner") return <Bug {...ICO} />;
  if (t === "csscolor" || t === "csstools") return <Braces {...ICO} />;
  if (t === "colorcontrastcheck") return <Contrast {...ICO} />;
  if (t === "dockerbuild") return <Container {...ICO} />;
  if (t === "dockerps") return <Boxes {...ICO} />;
  if (t === "dockerrun") return <Ship {...ICO} />;
  if (t === "k8s") return <Network {...ICO} />;
  if (t === "graphqlclient") return <Waypoints {...ICO} />;
  /* ---- computer use / vision (each gesture distinct) ---- */
  if (t === "screenshot") return <Camera {...ICO} />;
  if (t === "leftclick" || t === "rightclick" || t === "doubleclick" ||
      t === "longpress" || t === "click")
    return <MousePointer2 {...ICO} />;
  if (t === "swipe" || t === "drag" || t === "move") return <Move {...ICO} />;
  if (t === "scroll" || t === "wheel") return <Mouse {...ICO} />;
  if (t === "setvalue" || t === "type" || t === "key" || t === "input")
    return <Keyboard {...ICO} />;
  if (t === "getappstate" || t === "getwindowstate") return <AppWindow {...ICO} />;
  if (t === "listapps" || t === "listwindows" || t === "windows")
    return <LayoutGrid {...ICO} />;
  if (t === "openapplication" || t === "openapp") return <Rocket {...ICO} />;
  if (t === "requestaccess" || t === "checkaccess") return <ShieldCheck {...ICO} />;
  if (t === "zoom") return <ZoomIn {...ICO} />;
  if (t === "switchdisplay") return <MonitorDot {...ICO} />;
  if (t === "controlcomputer" || t === "computeruse" || t === "windowscontrol")
    return <Monitor {...ICO} />;
  if (t === "visionanalyze" || t === "vision" || t === "analyzeimage")
    return <Eye {...ICO} />;
  if (t === "imageocr" || t === "ocr") return <ScanText {...ICO} />;
  /* ---- generic doc / media / data fallbacks ---- */
  if (t.includes("database") || t === "sql" || t === "query")
    return <Database {...ICO} />;
  if (t.includes("doc") || t === "docx" || t === "pdf" || t === "pptx" || t === "xlsx")
    return <FileCode {...ICO} />;
  if (t === "image" || t === "generateimage") return <ImageIcon {...ICO} />;
  return <Wrench {...ICO} />;
}

/** Format raw tool name into human-readable label ("BrowserNavigate" → "Browser Navigate") */
function formatToolLabel(raw: string): string {
  if (!raw) return "Tool";
  // multi-word product names the camel-splitter would mangle ("Power Shell")
  const known = ["PowerShell"];
  if (known.includes(raw)) return raw;
  if (raw.includes(".")) {
    return raw.split(".").map(s => s.charAt(0).toUpperCase() + s.slice(1)).join(" ");
  }
  if (raw.includes("_")) return raw.split("_").map(s => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase()).join(" ");
  // Preserve known acronyms / caps: insert space before uppercase run
  const spaced = raw.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2");
  // Title-case single-letter fragments already fine
  return spaced;
}

/** row display name — always the real tool name, never a family bucket like "Explore" */
function rowLabel(e: ZTEntry): string {
  if (e.kind === "agent") return e.name ?? "SubAgent";
  if (e.kind === "compaction") return "Compressing context";
  if (e.kind === "reasoning") return "";
  if (e.name) return formatToolLabel(e.name);
  return e.family;
}

/* ------------------------------------------------------------------ */
/* status dot                                                          */
/* ------------------------------------------------------------------ */

function StatusDot({ e, running }: { e: ZTEntry; running: boolean }) {
  const cls = `zt-dot st-${e.status}${e.status === "in_progress" && running ? " spin" : ""}`;
  const inner =
    e.status === "in_progress" && running ? (
      <LoaderCircle size={12} strokeWidth={2} />
    ) : e.status === "completed" ? (
      <Check size={12} strokeWidth={2.2} />
    ) : e.status === "failed" ? (
      <X size={12} strokeWidth={2.2} />
    ) : (
      <Pause size={12} strokeWidth={2.2} />
    );
  return <span className={cls} aria-hidden="true">{inner}</span>;
}

/* ------------------------------------------------------------------ */
/* reasoning row — "Thinking" → "Thought · 5 seconds" (+ thought text)  */
/* ------------------------------------------------------------------ */

function ReasoningRow({ e, running }: { e: ZTEntry; running: boolean }) {
  const [open, setOpen] = useState(false);
  const live = e.status === "in_progress" && running;
  const dur = e.status !== "in_progress" && e.durationMs > 0
    ? fmtZcodeDuration(e.durationMs)
    : null;
  const label = live ? "Thinking" : e.status === "interrupted" ? "Thought" : "Thought";
  const hasText = !!e.thought;
  return (
    <div className="ztr" data-zcode-reasoning-status={e.status}>
      <button
        type="button"
        className={`ztr-trig${hasText ? " ztr-btn" : ""}${open ? " open self-start" : " full"}`}
        onClick={hasText ? () => setOpen(v => !v) : undefined}
        aria-expanded={hasText ? open : undefined}
        data-state={hasText ? (open ? "open" : "closed") : undefined}
      >
        {live ? <LoaderCircle {...ICO} className="ztr-spin" aria-hidden="true" /> : null}
        <Brain {...ICO} aria-hidden="true" />
        <span className="ztr-kind">{label}</span>
        {dur && <span className="ztr-dur">· {dur}</span>}
        {hasText && (
          <ChevronRight {...ICO} className="ztr-chev" aria-hidden="true" />
        )}
      </button>
      {hasText && open && (
        <div className="ztr-thought">{e.thought}</div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* tool row — name · args (mono) … duration + event-count for groups    */
/* ------------------------------------------------------------------ */

function ToolRow({ e, running }: { e: ZTEntry; running: boolean }) {
  const [open, setOpen] = useState(false);
  const dur = rowDurationMs(e, running);
  const live = e.status === "in_progress" && running;
  const label = rowLabel(e);
  const expandable = !!(e.args || e.detail);
  return (
    <div
      className="ztr ztr-tool"
      data-tool-call-name={e.name ?? e.family}
      data-tool-call-status={e.status}
      data-zcode-family={e.family}
      data-state={open ? "open" : "closed"}
    >
      <button
        type="button"
        className={`${expandable ? "ztr-btn " : "ztr-trig "}${open ? "open self-start" : "full"}`}
        onClick={expandable ? () => setOpen(v => !v) : undefined}
        aria-expanded={expandable ? open : undefined}
        data-state={expandable ? (open ? "open" : "closed") : undefined}
      >
        <span className="ztr-ico" aria-hidden="true">{toolIconFor(e.name)}</span>
        <span className="ztr-kind">{label}</span>
        {!open && e.args && <span className="ztr-args mono">{e.args}</span>}
        {!open && e.files.length > 0 && (
          <span className="ztr-files mono">
            {e.files.map(f => {
              const base = f.split(/[\\/]/).pop() ?? f;
              return (
                <span key={f} className="ztr-file" title={f}>
                  {base}
                </span>
              );
            })}
          </span>
        )}
        {live && (
          <LoaderCircle {...ICO} className="ztr-spin" aria-hidden="true" />
        )}
        {e.status === "failed" && <span className="ztr-tag">Failed</span>}
        {e.blocked && <span className="ztr-tag warn">waiting for approval</span>}
        {expandable && (
          <ChevronRight {...ICO} className={`ztr-chev${open ? "" : " end"}`} aria-hidden="true" />
        )}
      </button>
      {open && e.args && (
        <div className="ztr-panel">
          <div className="ztr-card">
            <div className="ztr-cmdwrap">
              <span className="ztr-ps" aria-hidden="true">$</span>
              <pre className="ztr-cmd">{e.args}</pre>
            </div>
            {e.detail && <pre className="ztr-out">{e.detail}</pre>}
            {dur !== null && dur > 0 && (
              <div className="ztr-footline">{fmtShortDuration(dur)}</div>
            )}
          </div>
        </div>
      )}
      {!open && e.error && (
        <span className="ztr-error mono" title={e.error}>{e.error}</span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* agent row — SubAgent branch; its rows nest beneath                   */
/* ------------------------------------------------------------------ */

function AgentRow({
  e,
  running,
  children,
}: {
  e: ZTEntry;
  running: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(true);
  const hasKids = children !== null && children !== undefined;
  const live = e.status === "in_progress" && running;
  return (
    <div className="ztr ztr-agent">
      <button
        type="button"
        className={`ztr-btn${open ? " open self-start" : " full"}`}
        onClick={hasKids ? () => setOpen(v => !v) : undefined}
        aria-expanded={hasKids ? open : undefined}
        data-state={hasKids ? (open ? "open" : "closed") : undefined}
      >
        <span className="ztr-ico" aria-hidden="true">{toolIconFor(e.name ?? "SubAgent")}</span>
        <span className="ztr-kind">{e.name}</span>
        {!open && <span className="ztr-agpill">SubAgent</span>}
        {!open && e.args && <span className="ztr-args mono">{e.args}</span>}
        {live && (
          <LoaderCircle {...ICO} className="ztr-spin" aria-hidden="true" />
        )}
        {e.status === "failed" && <span className="ztr-tag">Failed</span>}
        {hasKids && (
          <ChevronRight {...ICO} className={`ztr-chev${open ? "" : " end"}`} aria-hidden="true" />
        )}
      </button>
      {hasKids && open && <div className="ztr-children">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* compaction separator — the ZCode display:"separator" row             */
/* ------------------------------------------------------------------ */

function CompactionRow({ e }: { e: ZTEntry }) {
  const bits: string[] = [];
  if (e.tokens !== null) bits.push(`${fmtTokens(e.tokens)} tokens`);
  if (e.windowTokens !== null) bits.push(`window ${fmtTokens(e.windowTokens)}`);
  return (
    <div className="ztr-compact" role="separator">
      <span className="zc-line" aria-hidden="true" />
      <span className="zc-label">
        Compressing context{bits.length ? ` · ${bits.join(" · ")}` : ""}
      </span>
      <span className="zc-line" aria-hidden="true" />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* the list — top-level entries; agent entries carry nested rows locally */
/* ------------------------------------------------------------------ */

export function ZTList({
  model,
  running,
}: {
  model: ZTModel;
  running: boolean;
}) {
  const rows: ReactNode[] = [];
  for (const e of model.entries) {
    if (e.kind === "reasoning") rows.push(<ReasoningRow key={e.id} e={e} running={running} />);
    else if (e.kind === "tool")
      rows.push(
        e.parentAgent ? null : <ToolRow key={e.id} e={e} running={running} />
      );
    else if (e.kind === "compaction") rows.push(<CompactionRow key={e.id} e={e} />);
    else if (e.kind === "agent") {
      // the MAIN flow row ("universe" / legacy "general") is deleted per
      // the user's 2026-10-30 annotation — never render it; its tool rows
      // render top-level (fold hoists their parentAgent to null)
      if (e.parentAgent === null && (e.name === "universe" || e.name === "general")) continue;
      const kids = model.entries.filter(t => t.kind === "tool" && t.parentAgent === e.name);
      rows.push(
        <AgentRow key={e.id} e={e} running={running}>
          {kids.length ? kids.map(k => <ToolRow key={k.id} e={k} running={running} />) : null}
        </AgentRow>
      );
    }
  }
  return <div className="zt">{rows}</div>;
}

/* ------------------------------------------------------------------ */
/* StatusPanel — the chat.statusPanel.* sections, exact ZCode labels     */
/* ------------------------------------------------------------------ */

export function StatusPanel({
  model,
  running,
  queueMode,
  hints = true,
  onStop,
}: {
  model: ZTModel;
  running: boolean;
  queueMode: boolean;
  hints?: boolean;
  onStop?: () => void;
}) {
  const p = model.panel;
  const secs: ReactNode[] = [];

  if (p.providerHalt) {
    secs.push(
      <span key="halt" className="zsp-sec halt">
        <TriangleAlert size={12} strokeWidth={2} aria-hidden="true" />
        {p.providerHalt}
      </span>
    );
  } else {
    secs.push(
      <span key="live" className="zsp-sec">
        <StatusDot e={{ status: "in_progress" } as ZTEntry} running={running} />
        {running ? "Working" : p.outcome === "failed" ? "Failed" : p.outcome === "cancelled" ? "Stopped" : "Done"}
      </span>
    );
  }

  if (running) {
    secs.push(
      <span key="agents" className="zsp-sec">
        <b>Agents</b> {p.agentsRunning.length > 0
          ? `${p.agentsRunning.length} running`
          : p.agentsEnded > 0
            ? `Ended ${p.agentsEnded}`
            : ""}
      </span>
    );
  }
  if (p.todo && running) {
    const total = p.todo.items.length;
    const done = p.todo.items.filter(t => t.status === "completed").length;
    if (total > 0) {
      secs.push(
        <span key="todo" className="zsp-sec">
          <b>Progress</b> {done}/{total}
          {p.todo.idx !== null && p.todo.idx < total
            ? ` — ${shorten(p.todo.items[p.todo.idx]?.content ?? "")}`
            : ""}
        </span>
      );
    }
  }
  if (p.changesFiles > 0 || p.changesTools > 0) {
    secs.push(
      <span key="changes" className="zsp-sec">
        <b>Changes</b> {p.changesFiles} {p.changesFiles === 1 ? "file" : "files"}
      </span>
    );
  } else if (running) {
    secs.push(
      <span key="clean" className="zsp-sec">
        <b>Changes</b> Clean
      </span>
    );
  }
  if (p.terminals > 0) {
    secs.push(
      <span key="term" className="zsp-sec">
        <b>Terminals</b> {p.terminals}
      </span>
    );
  }
  if (p.environmentTools) {
    secs.push(
      <span key="git" className="zsp-sec">
        <b>Commit / Push</b>
      </span>
    );
  }
  if (p.workflowsEnded > 0) {
    secs.push(
      <span key="wf" className="zsp-sec">
        <b>Workflows</b> Ended {p.workflowsEnded}
      </span>
    );
  }
  const cu = p.contextUsage;
  if (cu.used !== null && cu.total !== null && cu.total > 0) {
    secs.push(
      <span key="cu" className="zsp-sec mono">
        Context usage {fmtTokens(cu.used)} / {fmtTokens(cu.total)}
        {cu.estimate ? " est." : ""}
      </span>
    );
  }

  return (
    <span className="zsp">
      {secs}
      <span className="zsp-sp" />
      {onStop && running && (
        <button type="button" className="zsp-stop" onClick={onStop}>
          Stop
        </button>
      )}
      {hints && (
        <span className="zsp-hint">
          {queueMode ? (
            <>
              <b>Enter queues</b> your follow-up · <b>Esc</b> stops
            </>
          ) : (
            <>
              <b>Enter steers</b> the running goal · <b>Esc</b> stops
            </>
          )}
        </span>
      )}
    </span>
  );
}

function shorten(s: string, n = 46): string {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}
