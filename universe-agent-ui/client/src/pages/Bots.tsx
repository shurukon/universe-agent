/**
 * pages/Bots.tsx — the Bots surface, rebuilt 2026-11 on Grok Bot's
 * persistent-agent design, rendered in the SAME Universe design system
 * (design-system.css tokens — the shell's exact surfaces, hairlines and
 * green accent).
 *
 *   SIDEBAR  the shell's left column in Bots mode (the ONLY surface that
 *            swaps the column body): the workspace switcher on top, then
 *            the roster — avatar-with-presence, name, last message, time —
 *            New bot (+ Create via chat) and the honest Connect to Muse
 *            Code state when the runtime is absent.
 *   CENTER   the selected bot's thread: presence header, approvals banner,
 *            a heterogeneous transcript (user bubbles right, bot panels
 *            left, system milestones centered, approval cards inline with
 *            Allow once / Always allow / Deny), and the composer card with
 *            a Meta one-shot mic (muse-voice-transcribe — model selected
 *            automatically, there is exactly one).
 *   RIGHT    the bot's computer, three levels of access (Grok Bot parity):
 *            Screen (live machine view), Timeline (LITERALLY the Universe
 *            timeline — the same ActivityPane/fold the chat renders, fed
 *            by the bot's real turn events on the same event bus), Details
 *            (identity, the SAME Universe tool inventory from the real
 *            registry, Routines with schedule presets, the TASK→SKILL→
 *            ROUTINE stepper), Library (the bots' shared computer).
 *
 * Data: lib/botsApi.ts against /api/bots*. The selected bot polls every 4s;
 * the roster every 10s. Bot tasks run as REAL turns through the same
 * orchestrator as chat (no Meta key → the Universe engine; Meta key →
 * Muse Code), so bots have literally the same tools and the same timeline.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  Bot as BotIcon,
  Check,
  ChevronDown,
  Clock,
  Cable,
  ExternalLink,
  FileText,
  Library,
  Loader2,
  Maximize2,
  Mic,
  MicOff,
  Monitor,
  PlayCircle,
  Plus,
  Save,
  ShieldAlert,
  Sparkles,
  Square,
  Trash2,
  Workflow,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { useUi } from "@/stores/ui";
import Sidebar from "@/components/desktop/Sidebar";
import ActivityPane from "@/components/desktop/ActivityPane";
import ConsentBanner from "@/components/desktop/ConsentBanner";
import { api } from "@/lib/api";
import { useChat } from "@/stores/chat";
import "../components/desktop/design-system.css";
import "../components/desktop/desktop.css";
import "../components/desktop/redesign.css";
import "../components/desktop/flow.css";
import "../components/desktop/bots.css";
import {
  botScreenUrl,
  clearBotSchedule,
  createBot,
  decideApproval,
  deleteBot,
  draftBot,
  getBot,
  listApprovals,
  listBots,
  listTools,
  runBot,
  setBotSchedule,
  setBotSkill,
  sharedFiles,
  stopBot,
  transcribeAudio,
  type Bot,
  type BotApproval,
  type BotDetail,
  type BotEvent,
  type BotSchedule,
  type SharedFile,
  type ToolInfo,
} from "@/lib/botsApi";

/* ---- presence ----------------------------------------------------------- */

const STATUS_LINE: Record<Bot["status"], string> = {
  idle: "idle — ready for work",
  running: "working",
  waiting_approval: "waiting for your approval",
  error: "hit an error",
  paused: "paused",
};

/** Identity hues — the decided Universe palette: blues, violets, magentas,
 *  cyans and crimsons. Green and orange are retired brand-wide (2026-11). */
const IDENTITY_HUES = [215, 230, 250, 262, 285, 310, 330, 350, 195, 175];

function statusHue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return IDENTITY_HUES[h % IDENTITY_HUES.length];
}

function BotAvatar({
  name,
  status,
  size = 30,
}: {
  name: string;
  status?: Bot["status"];
  size?: number;
}) {
  const hue = statusHue(name);
  return (
    <span
      className="bots-avatar"
      style={{
        width: size,
        height: size,
        fontSize: Math.max(11, size * 0.4),
        background: `linear-gradient(135deg, hsl(${hue} 42% 66%), hsl(${(hue + 40) % 360} 48% 52%))`,
      }}
      title={name}
    >
      {name.slice(0, 1).toUpperCase()}
      {status && (
        <span className={`bots-presence ${status}`} aria-hidden="true" />
      )}
    </span>
  );
}

function fmtClock(ts: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Grok-style at-a-glance routine label for a bot's schedule. */
function routineLabel(s: Bot["schedule"]): string {
  if (s.kind === "daily") return `Every day${s.time ? ` · ${s.time}` : ""}`;
  if (s.kind === "interval")
    return `Every ${s.expr?.replace(/m$/, " min") ?? "interval"}`;
  if (s.kind === "cron") return `Cron · ${s.expr ?? ""}`;
  return "";
}

/* split a bot report into prose + URLs (trailing punctuation stays prose) */
const URL_RE = /(https?:\/\/[^\s<>"')\]]+)/g;

function splitLinks(
  text: string
): Array<{ t: string; url?: string }> {
  const parts: Array<{ t: string; url?: string }> = [];
  let last = 0;
  for (const m of Array.from(text.matchAll(URL_RE))) {
    const i = m.index ?? 0;
    const url = m[0].replace(/[.,;:!?)\]]+$/, "");
    if (i > last) parts.push({ t: text.slice(last, i) });
    parts.push({ t: url, url });
    last = i + url.length;
  }
  if (last < text.length) parts.push({ t: text.slice(last) });
  return parts;
}

function hostOf(u: string): string {
  try {
    return new URL(u).host.replace(/^www\./, "");
  } catch {
    return u.slice(0, 28);
  }
}

function fmtDay(ts: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  if (d.toDateString() === now.toDateString())
    return fmtClock(ts) || "today";
  const y = new Date(now.getTime() - 86400000);
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/* ---- transcript bubbles ---------------------------------------------------- */

function EventBubble({ ev }: { ev: BotEvent }) {
  const time = fmtClock(ev.ts);
  if (ev.role === "user") {
    return (
      <div className="bots-ev user">
        {time && <span className="bots-ev-time">{time}</span>}
        <div className="bots-ev-user">{ev.text}</div>
      </div>
    );
  }
  if (ev.role === "bot") {
    // Grok Bot's result-card pattern: a structured report with a Done
    // dot, the prose with live links, and one chip per artifact URL.
    const parts = splitLinks(ev.text);
    const links = Array.from(new Set(parts.filter(p => p.url).map(p => p.url!)));
    return (
      <div className="bots-ev">
        <div className="bots-report">
          <div className="bots-report-head">
            <span className="bots-report-dot" aria-hidden="true" />
            <b>Report · done</b>
            {time && <span className="bots-ev-time">{time}</span>}
          </div>
          <div className="bots-report-body">
            {parts.map((p, i) =>
              p.url ? (
                <a key={i} href={p.url} target="_blank" rel="noopener noreferrer">
                  {p.t}
                </a>
              ) : (
                <span key={i}>{p.t}</span>
              )
            )}
          </div>
          {links.length > 0 && (
            <div className="bots-report-links">
              {links.map(u => (
                <a key={u} href={u} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="size-3" /> {hostOf(u)}
                </a>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }
  if (ev.role === "approval") {
    return (
      <div className="bots-ev">
        <div className="bots-ev-system" style={{ maxWidth: "100%" }}>
          {ev.text}
        </div>
      </div>
    );
  }
  const isErr = /^run failed:/i.test(ev.text);
  return (
    <div className="bots-ev">
      <div className={`bots-ev-system${isErr ? " err" : ""}`}>{ev.text}</div>
    </div>
  );
}

/* ---- create dialog (form + create-via-chat) ---------------------------------- */

function CreateBotDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (bot: Bot) => void;
}) {
  const [mode, setMode] = useState<"chat" | "form">("chat");
  const [describe, setDescribe] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [engine, setEngine] = useState(false);
  const [creating, setCreating] = useState(false);

  const draft = async () => {
    const text = describe.trim();
    if (!text) return;
    setDrafting(true);
    try {
      const d = await draftBot(text);
      setMode("form");
      setName(d.name);
      setDesc(d.description);
      setEngine(d.engine);
    } catch (e) {
      // honest degrade: the raw text becomes the draft
      setMode("form");
      setName(text.split(/\s+/).slice(0, 3).join(" ") || "New bot");
      setDesc(text);
      setEngine(false);
      toast.info("Drafted from your text", {
        description:
          e instanceof Error ? e.message : "the engine was unreachable",
      });
    } finally {
      setDrafting(false);
    }
  };

  const create = async () => {
    if (!name.trim()) return;
    setCreating(true);
    try {
      const bot = await createBot(name.trim(), desc.trim());
      toast.success(`${bot.name} joined your roster`);
      onCreated(bot);
    } catch (e) {
      toast.error("Could not create the bot", {
        description: e instanceof Error ? e.message : "bridge unreachable",
      });
    } finally {
      setCreating(false);
    }
  };

  return (
    <div
      className="bots-overlay"
      role="dialog"
      aria-modal="true"
      onMouseDown={e => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bots-dialog">
        {mode === "chat" ? (
          <>
            <h3>Create a bot via chat</h3>
            <p className="dlg-sub">
              Describe the teammate you need in one message — Universe drafts
              the name and the job definition, you confirm them.
            </p>
            <label htmlFor="bots-describe">What should this bot do?</label>
            <textarea
              id="bots-describe"
              autoFocus
              value={describe}
              onChange={e => setDescribe(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void draft();
                }
              }}
              placeholder="e.g. Scan my sources every morning and report the three things that matter back here…"
            />
            <div className="bots-dlg-actions">
              <button type="button" className="bots-btn" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="bots-btn primary"
                disabled={!describe.trim() || drafting}
                onClick={() => void draft()}
              >
                {drafting ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Sparkles className="size-3.5" />
                )}
                Draft the bot
              </button>
            </div>
          </>
        ) : (
          <>
            <h3>Create a bot</h3>
            <p className="dlg-sub">
              Bots are persistent teammates: identity, memory, their own
              workspace and the SAME Universe tools.
            </p>
            {engine && (
              <div className="bots-draft">
                <div className="bots-draft-row">
                  <Sparkles className="size-3.5 text-[var(--ds-accent,#2b5c9b)]" />
                  <b>Drafted by the engine</b>
                  <span className="bots-chip on rec">editable</span>
                </div>
              </div>
            )}
            <label htmlFor="bots-name">Name</label>
            <input
              id="bots-name"
              autoFocus
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Research Scout"
            />
            <label htmlFor="bots-desc">Job definition</label>
            <textarea
              id="bots-desc"
              value={desc}
              onChange={e => setDesc(e.target.value)}
              placeholder="What this bot owns, its sources and its boundaries…"
            />
            <div className="bots-dlg-actions">
              <button
                type="button"
                className="bots-btn"
                onClick={() => setMode("chat")}
              >
                Back
              </button>
              <button
                type="button"
                className="bots-btn primary"
                disabled={!name.trim() || creating}
                onClick={() => void create()}
              >
                {creating ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Plus className="size-3.5" />
                )}
                Create bot
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ---- the roster (sidebar body) --------------------------------------------- */

/* Grok Bot groups teammates by their standing schedule */
const ROSTER_GROUPS: Array<{
  id: string;
  label: string;
  match: (b: Bot) => boolean;
}> = [
  { id: "daily", label: "Daily", match: b => b.schedule.kind === "daily" },
  {
    id: "recurring",
    label: "Recurring",
    match: b => b.schedule.kind === "interval" || b.schedule.kind === "cron",
  },
  {
    id: "ondemand",
    label: "On demand",
    match: b => b.schedule.kind === "none",
  },
];

function Roster({
  bots,
  selectedId,
  onSelect,
  onNew,
  runtime,
  onRuntimeRefresh,
}: {
  bots: Bot[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNew: (mode: "chat" | "form") => void;
  runtime: { runtime: string; muse_installed: boolean; meta_key: boolean } | null;
  onRuntimeRefresh: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [museBusy, setMuseBusy] = useState(false);
  const [museMsg, setMuseMsg] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: PointerEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [menuOpen]);

  // one click: the bridge searches this machine for the Muse Code CLI and
  // installs it (official installer) when missing — then the runtime flips
  const installMuse = async () => {
    setMuseBusy(true);
    setMuseMsg("");
    try {
      const r = await api.botsMuseInstall();
      setMuseMsg(r.detail);
      toast(r.installed ? "Muse Code connected" : "Muse Code install failed", {
        description: r.detail,
      });
      if (r.installed) onRuntimeRefresh();
    } catch {
      setMuseMsg("bridge unreachable — is it running?");
    } finally {
      setMuseBusy(false);
    }
  };

  return (
    <div className="bots-roster">
      <div className="bots-roster-head">
        <h2>Bots</h2>
        <div className="bots-newmenu" ref={menuRef}>
          <button
            type="button"
            className="bots-btn primary"
            style={{ padding: "4px 10px", fontSize: "11.5px" }}
            onClick={() => setMenuOpen(v => !v)}
            aria-expanded={menuOpen}
          >
            <Plus className="size-3.5" /> New bot
          </button>
          {menuOpen && (
            <div className="cx-menu side-menu" role="menu">
              <div className="cx-menu-h">Create</div>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  onNew("chat");
                }}
              >
                <span>Create via chat</span>
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  onNew("form");
                }}
              >
                <span>New bot form</span>
              </button>
            </div>
          )}
        </div>
      </div>
      {runtime && !runtime.muse_installed && (
        <div className="bots-connect">
          <b>
            <Cable className="size-3.5" /> Connect to Muse Code
          </b>
          <p>
            Bots run on Meta's Muse Code runtime. Universe can find it on
            this machine, install it if it is missing, and connect it —
            until then tasks run on Universe's own engine.
          </p>
          <button
            type="button"
            className="bots-btn primary"
            disabled={museBusy}
            onClick={() => void installMuse()}
          >
            {museBusy ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Cable className="size-3.5" />
            )}
            {museBusy ? "Searching & installing…" : "Install & connect Muse Code"}
          </button>
          {museMsg && <p className="bots-connect-msg">{museMsg}</p>}
        </div>
      )}
      {runtime && runtime.muse_installed && !runtime.meta_key && (
        <div className="bots-connect">
          <b>
            <Sparkles className="size-3.5" /> Muse Code is installed
            <span className="bots-chip rec-chip rec">Meta · Recommended</span>
          </b>
          <p>
            Add your Meta API key in Settings → Providers → Meta to run bots
            on Muse Code. Until then tasks run on Universe's own engine.
          </p>
        </div>
      )}
      {bots.length === 0 ? (
        <p className="bots-roster-note">
          No bots yet — create your first teammate and hand it real work.
        </p>
      ) : (
        /* Grok Bot groups the roster by standing schedule: Daily,
           Recurring, then bots you message on demand. */
        <div>
          {ROSTER_GROUPS.map(g => {
            const rows = bots.filter(g.match);
            if (rows.length === 0) return null;
            return (
              <div key={g.id}>
                <div className="bots-group-label">{g.label}</div>
                <ul>
                  {rows.map(b => {
                    const last = b.last_result ?? "";
                    return (
                      <li key={b.id}>
                        <button
                          type="button"
                          className={`bots-row${selectedId === b.id ? " on" : ""}`}
                          onClick={() => onSelect(b.id)}
                        >
                          <BotAvatar
                            name={b.name}
                            status={b.status}
                            size={30}
                          />
                          <span className="bots-row-name">
                            <b>{b.name}</b>
                            <span>
                              {b.status === "running"
                                ? "working…"
                                : b.status === "waiting_approval"
                                  ? "needs your approval"
                                  : last
                                    ? last.slice(0, 64)
                                    : STATUS_LINE[b.status]}
                            </span>
                          </span>
                          <span className="bots-row-time">
                            {fmtDay(b.last_run_at)}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---- right panel tabs --------------------------------------------------------- */

function ScreenTab({ bot }: { bot: BotDetail }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setSrc(null);
    botScreenUrl(bot.id)
      .then(u => {
        if (alive) setSrc(u);
      })
      .catch(() => {
        if (alive) setSrc(null);
      });
    return () => {
      alive = false;
    };
  }, [bot.id]);
  return (
    <div className="bots-screen">
      <p
        style={{
          margin: 0,
          fontSize: "11.5px",
          color: "var(--ink3,#999999)",
        }}
      >
        The bot's computer — a live, honest view of its status, transcript and
        workspace.
      </p>
      <div className="bots-screen-frame">
        {src ? (
          <iframe
            key={bot.id}
            src={src}
            title={`${bot.name} — live screen`}
          />
        ) : (
          <div className="bots-hero" style={{ padding: 12 }}>
            <Loader2 className="size-4 animate-spin" />
          </div>
        )}
      </div>
      {src && (
        <button
          type="button"
          className="bots-btn"
          style={{ alignSelf: "flex-start" }}
          onClick={() => window.open(src, "_blank", "noopener")}
        >
          <Maximize2 className="size-3.5" /> Open in a new window
        </button>
      )}
    </div>
  );
}

const STEPS = [
  { icon: PlayCircle, title: "TASK", hint: "Run the job once and watch it" },
  { icon: Save, title: "SKILL", hint: "Save the method as a skill" },
  { icon: Clock, title: "ROUTINE", hint: "Run on a schedule" },
];

function Stepper({ bot }: { bot: Bot }) {
  const current = bot.schedule.kind !== "none" ? 2 : bot.skill ? 1 : 0;
  return (
    <div className="bots-steps">
      {STEPS.map((s, i) => (
        <div
          key={s.title}
          className={`bots-step${i < current ? " done" : i === current ? " active" : ""}`}
        >
          <b>
            <s.icon className="size-3" /> {s.title}
          </b>
          <span>{s.hint}</span>
        </div>
      ))}
    </div>
  );
}

const SCHEDULE_PRESETS: Array<{ label: string; sched: BotSchedule }> = [
  { label: "No schedule", sched: { kind: "none" } },
  { label: "Every day · 8:00 AM", sched: { kind: "daily", time: "08:00", tz: "UTC" } },
  { label: "Every day · 6:00 PM", sched: { kind: "daily", time: "18:00", tz: "UTC" } },
  { label: "Every 30 minutes", sched: { kind: "interval", expr: "30m" } },
  { label: "Every 6 hours", sched: { kind: "interval", expr: "360m" } },
];

function DetailsTab({ bot }: { bot: BotDetail }) {
  const [tools, setTools] = useState<ToolInfo[] | null>(null);
  const [skillName, setSkillName] = useState("");
  const [sched, setSched] = useState<string>(
    bot.schedule.kind === "none" ? "none" : "custom"
  );
  useEffect(() => {
    let alive = true;
    listTools()
      .then(t => {
        if (alive) setTools(t);
      })
      .catch(() => {
        if (alive) setTools([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  const saveSkill = async () => {
    const n = skillName.trim();
    if (!n) return;
    try {
      await setBotSkill(bot.id, n);
      setSkillName("");
      toast.success(`Method saved as skill /${n.replace(/^\/+/, "")}`);
    } catch (e) {
      toast.error("Could not save the skill", {
        description: e instanceof Error ? e.message : "bridge unreachable",
      });
    }
  };

  const applySchedule = async (value: string) => {
    setSched(value);
    try {
      if (value === "none") {
        await clearBotSchedule(bot.id);
        toast.success("Routine cleared");
      } else {
        const preset = SCHEDULE_PRESETS.find(p => p.label === value);
        if (preset) {
          await setBotSchedule(bot.id, preset.sched);
          toast.success(`Routine set — ${preset.label}`);
        }
      }
    } catch (e) {
      toast.error("Could not set the routine", {
        description: e instanceof Error ? e.message : "bridge unreachable",
      });
    }
  };

  return (
    <div>
      <section className="bots-sec">
        <h3>
          <BotIcon className="size-3" /> Identity
        </h3>
        <p>{bot.description || "No job definition yet."}</p>
        <p className="sub">
          Workspace {bot.workspace?.split("/").slice(-1)[0] || bot.id} · joined{" "}
          {fmtDay(bot.created_at)}
        </p>
      </section>

      <section className="bots-sec">
        <h3>
          <Workflow className="size-3" /> Tools — the same as Universe
        </h3>
        {tools === null ? (
          <p className="sub">Reading the tool registry…</p>
        ) : (
          <>
            <p className="sub" style={{ marginTop: 0 }}>
              {tools.length} built-in tools, resolved through the same
              registry every Universe turn uses — file, shell, browser, web,
              the Studio tools, all of it.
            </p>
            <div className="bots-tools">
              {tools.slice(0, 14).map(t => (
                <div className="bots-tool" key={t.name} title={t.description}>
                  <b>{t.name}</b>
                  <span>{t.description}</span>
                </div>
              ))}
            </div>
            {tools.length > 14 && (
              <p className="sub">…and {tools.length - 14} more.</p>
            )}
          </>
        )}
      </section>

      <section className="bots-sec">
        <h3>
          <Clock className="size-3" /> Routines
        </h3>
        <p className="sub" style={{ marginTop: 0 }}>
          Give the bot a standing responsibility — it runs on a schedule and
          reports back here.
        </p>
        <div className="bots-fieldrow">
          <select
            value={sched}
            onChange={e => void applySchedule(e.target.value)}
            aria-label="Routine schedule"
          >
            {SCHEDULE_PRESETS.map(p => (
              <option key={p.label} value={p.label}>
                {p.label}
              </option>
            ))}
            <option value="custom" disabled>
              Custom…
            </option>
          </select>
        </div>
        {bot.schedule.kind !== "none" && (
          <p className="sub">
            Active: {bot.schedule.kind}
            {bot.schedule.time ? ` at ${bot.schedule.time}` : ""}
            {bot.schedule.expr ? ` (${bot.schedule.expr})` : ""}
            {bot.schedule.tz ? ` · ${bot.schedule.tz}` : ""}
          </p>
        )}
      </section>

      <section className="bots-sec">
        <h3>
          <Sparkles className="size-3" /> Task → Skill → Routine
        </h3>
        <Stepper bot={bot} />
        {bot.skills.length > 0 && (
          <div className="bots-chiprow" style={{ marginTop: 10 }}>
            {bot.skills.map(s => (
              <span key={s} className="bots-chip on">
                {s}
              </span>
            ))}
          </div>
        )}
        <div className="bots-fieldrow">
          <input
            value={skillName}
            onChange={e => setSkillName(e.target.value)}
            placeholder="Save this bot's method as a skill…"
            onKeyDown={e => {
              if (e.key === "Enter") void saveSkill();
            }}
          />
          <button
            type="button"
            className="bots-btn"
            disabled={!skillName.trim()}
            onClick={() => void saveSkill()}
          >
            <Save className="size-3.5" /> Save
          </button>
        </div>
      </section>
    </div>
  );
}

function LibraryTab({ botId }: { botId: string }) {
  const [data, setData] = useState<{ path: string; files: SharedFile[] } | null>(
    null
  );
  const load = useCallback(() => {
    sharedFiles()
      .then(setData)
      .catch(() => {});
  }, []);
  useEffect(() => {
    load();
    const id = window.setInterval(load, 8000);
    return () => window.clearInterval(id);
  }, [load]);
  return (
    <div className="bots-pane-pad">
      <h3
        style={{
          margin: "0 0 4px",
          fontSize: "10.5px",
          fontWeight: 600,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "var(--ink3,#999999)",
        }}
      >
        Shared computer
      </h3>
      <p style={{ margin: "0 0 10px", fontSize: "11.5px", color: "var(--ink3,#999999)" }}>
        One directory every bot reads and writes — the files substrate bot
        collaboration flows through.
      </p>
      {data && data.files.length === 0 && (
        <p style={{ fontSize: "12px", color: "var(--ink3,#999999)" }}>
          (empty — bots write their outputs here)
        </p>
      )}
      {data?.files.map(f => (
        <div className="bots-file" key={f.name} title={f.name}>
          <FileText className="size-3.5" style={{ flex: "none" }} />
          <span style={{ margin: 0, color: "inherit", fontSize: "inherit" }}>
            {f.name}
          </span>
          <span>{f.size}B</span>
        </div>
      ))}
      {data && (
        <p
          style={{
            margin: "10px 0 0",
            fontSize: "10.5px",
            color: "var(--ink4,#5e5e5e)",
          }}
        >
          {data.path}
        </p>
      )}
      {botId === "" && null}
    </div>
  );
}

/* ---- the page ------------------------------------------------------------------ */

type PanelTab = "screen" | "timeline" | "details" | "library";

export default function BotsPage() {
  const sidebarOpen = useUiSidebar();
  const [bots, setBots] = useState<Bot[]>([]);
  const [runtime, setRuntime] = useState<{
    runtime: string;
    muse_installed: boolean;
    meta_key: boolean;
  } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<BotDetail | null>(null);
  const [approvals, setApprovals] = useState<BotApproval[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelTab, setPanelTab] = useState<PanelTab>("screen");
  const [createOpen, setCreateOpen] = useState<null | "chat" | "form">(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const lastTurnRef = useRef<string | null>(null);

  const refreshList = useCallback(() => {
    listBots()
      .then(setBots)
      .catch(() => {});
  }, []);

  const refreshRuntime = useCallback(() => {
    // bots/status lives on the same bridge — a thin fetch here keeps the
    // roster banner honest (Connect to Muse Code vs ready)
    api
      .botsStatus()
      .then(setRuntime)
      .catch(() => {});
  }, []);

  useEffect(() => {
    refreshList();
    refreshRuntime();
    const id = window.setInterval(() => {
      refreshList();
      refreshRuntime();
    }, 10000);
    return () => window.clearInterval(id);
  }, [refreshList, refreshRuntime]);

  // selected bot: poll the detail every 4s
  const refreshDetail = useCallback(() => {
    if (!selectedId) return;
    getBot(selectedId)
      .then(d => {
        setDetail(d);
        if (d.last_turn_id && d.last_turn_id !== lastTurnRef.current) {
          lastTurnRef.current = d.last_turn_id;
          // refill the SAME Universe timeline for this bot's turn
          api
            .refill(d.last_turn_id, 0)
            .then(r => {
              for (const ev of r.events ?? [])
                useChat.getState().ingestEvent(ev as never);
            })
            .catch(() => {});
        }
      })
      .catch(() => {});
    listApprovals(selectedId)
      .then(setApprovals)
      .catch(() => {});
  }, [selectedId]);

  useEffect(() => {
    refreshDetail();
    if (!selectedId) return;
    const id = window.setInterval(refreshDetail, 4000);
    return () => window.clearInterval(id);
  }, [refreshDetail, selectedId]);

  // auto-open the panel with the Screen when the bot starts working
  useEffect(() => {
    if (detail?.status === "running" && !panelOpen) {
      setPanelOpen(true);
      setPanelTab("screen");
    }
  }, [detail?.status, panelOpen]);

  // keep the transcript pinned to the newest event
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [detail?.transcript.length, approvals.length]);

  const select = (id: string) => {
    setSelectedId(id);
    setDetail(null);
    setApprovals([]);
    lastTurnRef.current = null;
    setPanelOpen(false);
    setText("");
  };

  const send = async () => {
    if (!selectedId || !detail) return;
    const t = text.trim();
    if (!t || sending) return;
    if (detail.status === "running") {
      toast.info(`${detail.name} is already working`, {
        description: "Stop the current run first, or wait for it to finish.",
      });
      return;
    }
    setSending(true);
    setText("");
    try {
      const r = await runBot(selectedId, t);
      if ((r as { approval_required?: boolean }).approval_required) {
        toast.info("Held for approval", {
          description: "This task looks like a risky step — decide in the banner.",
        });
      }
      refreshDetail();
      if (!(r as { approval_required?: boolean }).approval_required) {
        setPanelOpen(true);
      }
    } catch (e) {
      toast.error("Could not hand off the task", {
        description: e instanceof Error ? e.message : "bridge unreachable",
      });
    } finally {
      setSending(false);
    }
  };

  const stop = async () => {
    if (!selectedId) return;
    try {
      await stopBot(selectedId);
      toast.success(`${detail?.name ?? "Bot"} stopped`);
      refreshDetail();
    } catch (e) {
      toast.error("Could not stop the run", {
        description: e instanceof Error ? e.message : "bridge unreachable",
      });
    }
  };

  const decide = async (approvalId: string, decision: "allow_once" | "always_allow" | "deny") => {
    if (!selectedId) return;
    try {
      await decideApproval(selectedId, approvalId, decision);
      refreshDetail();
    } catch (e) {
      toast.error("Could not record the decision", {
        description: e instanceof Error ? e.message : "bridge unreachable",
      });
    }
  };

  const remove = async () => {
    if (!selectedId || !detail) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      window.setTimeout(() => setConfirmDelete(false), 3200);
      return;
    }
    try {
      await deleteBot(selectedId);
      toast.success(`${detail.name} removed`);
      setSelectedId(null);
      setDetail(null);
      refreshList();
    } catch (e) {
      toast.error("Could not delete the bot", {
        description: e instanceof Error ? e.message : "bridge unreachable",
      });
    }
  };

  // mic: MediaRecorder → /api/meta/transcribe → text into the composer
  const toggleMic = async () => {
    if (recording) {
      recorderRef.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = e => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        if (blob.size < 1200) return;
        toast.info("Transcribing…", {
          description: "Meta · muse-voice-transcribe-1.0 (model auto-selected)",
        });
        try {
          const t = await transcribeAudio(blob);
          if (t) {
            setText(prev => (prev ? `${prev} ${t}` : t));
            taRef.current?.focus();
          }
        } catch (e) {
          toast.error("Transcription failed", {
            description:
              e instanceof Error ? e.message : "check the Meta API key in Settings",
          });
        }
      };
      recorderRef.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      toast.error("Microphone unavailable", {
        description: "Grant microphone access to speak to this bot.",
      });
    }
  };

  const onCreated = (bot: Bot) => {
    setCreateOpen(null);
    refreshList();
    setSelectedId(bot.id);
  };

  const transcript = detail?.transcript ?? [];
  const running = detail?.status === "running";
  const pendingApprovals = approvals;

  return (
    <div className="app-root">
      <div className={`app${sidebarOpen ? "" : " sb-closed"}`}>
        <Sidebar
          open={sidebarOpen}
          surface="bots"
          botsBody={
            <Roster
              bots={bots}
              selectedId={selectedId}
              onSelect={select}
              onNew={mode => setCreateOpen(mode)}
              runtime={runtime}
              onRuntimeRefresh={refreshRuntime}
            />
          }
        />
        <main className="bots-main">
          {!detail ? (
            <div className="bots-hero">
              <BotAvatar name="B" size={52} />
              <h1>Your always-on teammates</h1>
              <p>
                Bots are persistent agents with their own workspace, memory and
                the SAME Universe tools. Message one like a colleague — it
                works, reports back here, and only pulls you in when a step
                needs your approval.
              </p>
              <div className="bots-hero-actions">
                <button
                  type="button"
                  className="bots-btn primary"
                  onClick={() => setCreateOpen("chat")}
                >
                  <Sparkles className="size-3.5" /> Create via chat
                </button>
                <button
                  type="button"
                  className="bots-btn"
                  onClick={() => setCreateOpen("form")}
                >
                  <Plus className="size-3.5" /> New bot
                </button>
              </div>
              {bots.length > 0 && (
                <p style={{ fontSize: 11.5, color: "var(--ink4,#5e5e5e)" }}>
                  {bots.length} on the roster — pick one on the left.
                </p>
              )}
            </div>
          ) : (
            <>
              <header className="bots-head">
                <BotAvatar
                  name={detail.name}
                  status={detail.status}
                  size={32}
                />
                <div className="bots-head-name">
                  <b>{detail.name}</b>
                  <span title={transcript.at(-1)?.text ?? undefined}>
                    {running
                      ? "working — watch the Timeline"
                      : STATUS_LINE[detail.status]}
                  </span>
                </div>
                {routineLabel(detail.schedule) && (
                  <span
                    className="bots-chip"
                    title="Standing routine — it runs and reports back here"
                  >
                    <Clock className="size-3" /> {routineLabel(detail.schedule)}
                  </span>
                )}
                <button
                  type="button"
                  className={`bots-ptab${panelOpen && panelTab === "screen" ? " on" : ""}`}
                  onClick={() => {
                    setPanelOpen(true);
                    setPanelTab("screen");
                  }}
                >
                  <Monitor className="size-3.5" /> Screen
                </button>
                <button
                  type="button"
                  className={`bots-ptab${panelOpen && panelTab === "timeline" ? " on" : ""}`}
                  onClick={() => {
                    setPanelOpen(true);
                    setPanelTab("timeline");
                  }}
                >
                  <Clock className="size-3.5" /> Timeline
                </button>
                <button
                  type="button"
                  className={`bots-ptab${panelOpen && panelTab === "details" ? " on" : ""}`}
                  onClick={() => {
                    setPanelOpen(true);
                    setPanelTab("details");
                  }}
                >
                  <Workflow className="size-3.5" /> Details
                </button>
                <span className="cx-pop">
                  <button
                    type="button"
                    className="bots-iconbtn"
                    title={confirmDelete ? "Click again to confirm" : "Delete bot"}
                    onClick={() => void remove()}
                  >
                    <Trash2 className="size-4" />
                  </button>
                </span>
                <button
                  type="button"
                  className="bots-iconbtn"
                  title={panelOpen ? "Close the panel" : "Open the panel"}
                  onClick={() => setPanelOpen(v => !v)}
                >
                  {panelOpen ? (
                    <X className="size-4" />
                  ) : (
                    <ChevronDown
                      className="size-4"
                      style={{ transform: "rotate(-90deg)" }}
                    />
                  )}
                </button>
              </header>

              {pendingApprovals.length > 0 && (
                /* Grok Bot's Computer card: the bot holds on a step that
                   needs a human — the live screen is one click away and
                   the decision controls stay honest. */
                <div className="bots-approvals computer">
                  <div className="bots-comp-head">
                    <Monitor className="size-3.5" />
                    <b>Computer</b>
                    <span className="bots-chip warn">
                      <ShieldAlert className="size-3" /> Action needed
                    </span>
                    <button
                      type="button"
                      className="bots-btn"
                      title="Open the bot's live screen and take over"
                      onClick={() => {
                        setPanelOpen(true);
                        setPanelTab("screen");
                      }}
                    >
                      <Maximize2 className="size-3.5" /> Take over
                    </button>
                  </div>
                  {pendingApprovals.map(a => (
                    <div
                      key={a.id}
                      className="bots-comp-ask"
                      style={{
                        marginBottom:
                          pendingApprovals.length > 1 ? 10 : 0,
                      }}
                    >
                      <p>
                        {a.summary}
                        <span className="bots-comp-kind">
                          {" "}· {a.kind.replace(/_/g, " ")}
                        </span>
                      </p>
                      <div className="bots-approvals-actions">
                        <button
                          type="button"
                          className="bots-btn primary"
                          onClick={() => void decide(a.id, "allow_once")}
                        >
                          Allow once
                        </button>
                        <button
                          type="button"
                          className="bots-btn"
                          onClick={() => void decide(a.id, "always_allow")}
                        >
                          Always allow
                        </button>
                        <button
                          type="button"
                          className="bots-btn danger"
                          onClick={() => void decide(a.id, "deny")}
                        >
                          Deny
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <ConsentBanner />

              <div className="bots-transcript" ref={scrollRef}>
                <div className="bots-thread">
                  {transcript.length === 0 && (
                    <div className="bots-ev">
                      <div className="bots-ev-system">
                        Say what you need — {detail.name} runs it with the
                        same Universe tools and reports back here.
                      </div>
                    </div>
                  )}
                  {transcript.map((ev, i) => (
                    <EventBubble key={`${ev.ts}-${i}`} ev={ev} />
                  ))}
                  {running && (
                    <div className="bots-ev">
                      <div className="bots-ev-system">
                        <Loader2
                          className="size-3 animate-spin"
                          style={{ display: "inline", verticalAlign: "-2px", marginRight: 6 }}
                        />
                        working — tool calls stream in the Timeline
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="bots-composer">
                <div className="bots-composer-card">
                  <button
                    type="button"
                    className={`bots-iconbtn${recording ? " rec" : ""}`}
                    title={
                      recording ? "Stop recording" : "Speak (Meta · one-shot)"
                    }
                    onClick={() => void toggleMic()}
                  >
                    {recording ? (
                      <MicOff className="size-4" />
                    ) : (
                      <Mic className="size-4" />
                    )}
                  </button>
                  <textarea
                    ref={taRef}
                    rows={1}
                    value={text}
                    placeholder={`Message ${detail.name}…`}
                    onChange={e => setText(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        void send();
                      }
                    }}
                  />
                  {running ? (
                    <button
                      type="button"
                      className="bots-iconbtn"
                      title="Stop the run"
                      onClick={() => void stop()}
                    >
                      <Square className="size-4" />
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="bots-iconbtn send"
                      title="Send"
                      disabled={!text.trim() || sending}
                      onClick={() => void send()}
                    >
                      {sending ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <ArrowUp className="size-4" />
                      )}
                    </button>
                  )}
                </div>
              </div>
            </>
          )}
        </main>

        {panelOpen && detail && (
          <aside className="bots-panel" aria-label="Bot panel">
            <div className="bots-panel-tabs">
              {(
                [
                  ["screen", "Screen", Monitor],
                  ["timeline", "Timeline", Clock],
                  ["details", "Details", Workflow],
                  ["library", "Library", Library],
                ] as const
              ).map(([id, label, Icon]) => (
                <button
                  key={id}
                  type="button"
                  className={`bots-ptab${panelTab === id ? " on" : ""}`}
                  onClick={() => setPanelTab(id)}
                >
                  <Icon className="size-3.5" /> {label}
                </button>
              ))}
              <span style={{ flex: 1 }} />
              <button
                type="button"
                className="bots-iconbtn"
                title="Close the panel"
                onClick={() => setPanelOpen(false)}
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="bots-panel-body">
              {panelTab === "screen" && <ScreenTab bot={detail} />}
              {panelTab === "timeline" && (
                <div className="bots-pane">
                  <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
                    {detail.last_turn_id ? (
                      <ActivityPane
                        goalId={detail.last_turn_id}
                        title={`${detail.name} · Timeline`}
                      />
                    ) : (
                      <div className="bots-pane-pad">
                        <p style={{ fontSize: "12.5px", color: "var(--ink3,#999999)" }}>
                          No run yet — hand {detail.name} a task and its run
                          streams here on the SAME Universe timeline: plan,
                          tool calls, durations, artifacts.
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              )}
              {panelTab === "details" && <DetailsTab bot={detail} />}
              {panelTab === "library" && <LibraryTab botId={detail.id} />}
            </div>
          </aside>
        )}
      </div>

      {createOpen && (
        <CreateBotDialog
          onClose={() => setCreateOpen(null)}
          onCreated={onCreated}
        />
      )}
    </div>
  );
}

/* ---- the ui store slice (sidebar open) — the same source as the shell --- */

function useUiSidebar(): boolean {
  return useUi(s => s.sidebarOpen);
}
