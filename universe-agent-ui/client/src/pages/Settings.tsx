/**
 * pages/Settings.tsx — Phase 3: the ZCode-literal Settings page
 * (report-02 layout) over the Linear-discipline content cards.
 *
 * Layout (report §structure): a left sections column grouped as
 *   Basics            — General · Appearance · Model settings · Browser Use
 *                       · Computer Use (+ Voice, a Universe extra ZCode has
 *                       no equivalent for)
 *   Agent capabilities— Memory · Subagents · MCP Servers ·
 *                       Skills · Commands · Hooks
 *   Data and statistics— Usage stats · Indexing
 * with Back to workspace on top, the user card, an Onboard button under
 * the sections and a Window menu (minimize/maximize/close via universe.win).
 * Model settings opens by default (report §1); ?section= deep links land
 * on their pane (legacy ids providers/agents/mcp keep working).
 *
 * Content panes — every control bound to a confirmed bridge endpoint:
 *   General   — PUT /api/settings {assistant_name, response_language,
 *               project_root (native folder picker)} + the report §2.4/§2.5
 *               mapping: permission mode (PUT /api/permission-mode),
 *               interaction_behavior (steer|queue), auto-continue timer,
 *               default thinking level, chat-view grouping toggles and the
 *               attachments switch (all Phase-3 flags, default on)
 *   Model settings — GET /api/providers/catalog (+local_reachable) ·
 *               GET /api/providers (masked key inventory) ·
 *               POST /api/providers (live verify; re-install APPENDS a key) ·
 *               GET/POST/DELETE /api/providers/{name}/keys (unlimited keys) ·
 *               GET/POST .../model · DELETE /api/providers/{name}
 *   Voice     — PUT /api/settings {voice_enabled, wake_word, stt_*, tts_*} ·
 *               POST /api/voice/check (pre-check, real error shown)
 *   Subagents — GET /api/agents (read-only roster; the model is
 *               picked from the composer chip, never here)
 *   MCP Servers — GET/POST/DELETE/PATCH /api/mcp/servers
 *   Memory    — GET /api/memory (live counts from universe.db, no secrets)
 *   Hooks     — PUT /api/settings {hooks_enabled, hooks[]} (the kill
 *               switch HookManager._load actually reads + per-rule edit)
 *   Appearance/Browser Use/Computer Use/Skills/Commands/
 *   Usage stats/Indexing — honest placeholders (not wired yet / out of scope)
 *
 * Styles: components/desktop/settings-linear.css (classes sx-*).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "wouter";
import {
  Anchor,
  ArrowLeft,
  ChartColumn,
  Bot,
  Brain,
  Check,
  ChevronDown,
  Cpu,
  Database,
  FolderOpen,
  Globe,
  LoaderCircle,
  Mic,
  Minus,
  Monitor,
  Palette,
  Pencil,
  Plug,
  Plus,
  Settings2,
  Sparkles,
  Square,
  SquareTerminal,
  Trash2,
  X,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import {
  api,
  BridgeError,
  invalidateModelsCache,
  type AgentInfo,
  type CatalogProvider,
  type CustomCommand,
  type HookRule,
  type McpServerInfo,
  type ProviderKeyInfo,
  type Settings,
  type SkillInfo,
} from "@/lib/api";
import { useChat } from "@/stores/chat";
import { useSetup } from "@/stores/setup";
import ConfirmDialog, {
  type ConfirmSpec,
} from "@/components/desktop/ConfirmDialog";
import "../components/desktop/settings-linear.css";

/** shared in-app confirm state — every destructive action on this page
 *  (provider remove, MCP remove) opens the ConfirmDialog card, never
 *  window.confirm */
let askConfirmImpl: (spec: ConfirmSpec) => void = () => {};
function askConfirm(spec: ConfirmSpec) {
  askConfirmImpl(spec);
}

type TabId =
  | "general"
  | "appearance"
  | "providers"
  | "browser-use"
  | "computer-use"
  | "voice"
  | "memory"
  | "agents"
  | "plugins"
  | "mcp"
  | "skills"
  | "commands"
  | "hooks"
  | "usage-stats"
  | "indexing";

interface SectionDef {
  id: TabId;
  label: string;
  icon: LucideIcon;
}

/** the report-02 sections column: three groups, Model settings inside
 *  Basics. Voice is a Universe extra (ZCode has no voice channel) — kept so
 *  the working voice settings never lose their home. */
const GROUPS: Array<{ label: string; items: SectionDef[] }> = [
  {
    label: "Basics",
    items: [
      { id: "general", label: "General", icon: Settings2 },
      { id: "appearance", label: "Appearance", icon: Palette },
      { id: "providers", label: "Model settings", icon: Cpu },
      { id: "browser-use", label: "Browser Use", icon: Globe },
      { id: "computer-use", label: "Computer Use", icon: Monitor },
      { id: "voice", label: "Voice", icon: Mic },
    ],
  },
  {
    label: "Agent capabilities",
    items: [
      { id: "memory", label: "Memory", icon: Brain },
      { id: "agents", label: "Subagents", icon: Bot },
      { id: "mcp", label: "MCP Servers", icon: Plug },
      { id: "skills", label: "Skills", icon: Sparkles },
      { id: "commands", label: "Commands", icon: SquareTerminal },
      { id: "hooks", label: "Hooks", icon: Anchor },
    ],
  },
  {
    label: "Data and statistics",
    items: [
      { id: "usage-stats", label: "Usage stats", icon: ChartColumn },
      { id: "indexing", label: "Indexing", icon: Database },
    ],
  },
];

const ALL_SECTIONS: SectionDef[] = GROUPS.flatMap(g => g.items);

function UsageStatsTab() {
  const [data, setData] = React.useState<{
    totalTokens: { input: number; output: number };
    calls: number;
    byProvider: Array<{
      provider: string;
      input: number;
      output: number;
      calls: number;
    }>;
    daily: Array<{ date: string; input: number; output: number }>;
  } | null>(null);
  const [range, setRange] = React.useState("7d");
  React.useEffect(() => {
    api
      .getUsage(range)
      .then(setData)
      .catch(() => setData(null));
  }, [range]);
  if (!data) return <p className="sx-sub">Loading…</p>;
  if (!data.calls)
    return (
      <p className="sx-sub">
        No activity yet — run a goal to see token usage here.
      </p>
    );
  return (
    <>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {["7d", "30d", "all"].map(r => (
          <button
            key={r}
            className={`sx-btn ${range === r ? "on" : ""}`}
            onClick={() => setRange(r)}
          >
            {r}
          </button>
        ))}
      </div>
      <div className="sx-statgrid">
        <div className="sx-stat">
          <b>{data.totalTokens.input.toLocaleString()}</b>
          <span>Input tokens</span>
        </div>
        <div className="sx-stat">
          <b>{data.totalTokens.output.toLocaleString()}</b>
          <span>Output tokens</span>
        </div>
        <div className="sx-stat">
          <b>{data.calls}</b>
          <span>Calls</span>
        </div>
      </div>
      {data.byProvider.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {data.byProvider.map(p => (
            <div key={p.provider} className="sx-row">
              <span>{p.provider}</span>
              <span className="mono">
                {p.input.toLocaleString()} / {p.output.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}
      {data.daily.length > 1 && (
        <div style={{ marginTop: 12, display: "flex", gap: 4 }}>
          {data.daily.slice(-14).map(d => (
            <div
              key={d.date}
              title={`${d.date}: ${d.input}/${d.output}`}
              style={{
                flex: 1,
                height: 24,
                background: d.input + d.output > 0 ? "#38C8FF" : "#2b2b2b",
                borderRadius: 4,
              }}
            />
          ))}
        </div>
      )}
    </>
  );
}

/** ?section= deep links: the report names (model-settings, mcp-servers…)
 *  and the legacy Phase-2 ids (providers, agents, mcp) both land right. */
const SECTION_ALIAS: Record<string, TabId> = {
  general: "general",
  appearance: "appearance",
  providers: "providers",
  "model-settings": "providers",
  "browser-use": "browser-use",
  "computer-use": "computer-use",
  voice: "voice",
  memory: "memory",
  agents: "agents",
  subagents: "agents",
  mcp: "mcp",
  "mcp-servers": "mcp",
  skills: "skills",
  commands: "commands",
  hooks: "hooks",
  "usage-stats": "usage-stats",
  indexing: "indexing",
};

const STT_PROVIDERS = ["local", "openai", "groq"];
const TTS_PROVIDERS = ["edge-tts", "openai", "fish-audio", "elevenlabs"];

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="sx-field">
      <span className="sx-flabel">{label}</span>
      {children}
      {hint && <span className="sx-hint">{hint}</span>}
    </label>
  );
}

/** a labeled switch row (report §2 toggles) — role=switch so the state
 *  is queryable by tests and screen readers alike */
function Switch({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div className="sx-swrow">
      <span className="sx-swtext">
        <b>{label}</b>
        {hint && <span className="sx-hint">{hint}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        className={`sx-switch${checked ? " on" : ""}`}
        disabled={disabled}
        onClick={() => onChange(!checked)}
      >
        <span className="sx-knob" aria-hidden="true" />
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* general                                                             */
/* ------------------------------------------------------------------ */

/** typed slice of the electron preload — pickFolder exists only when the
 *  app runs inside Electron; a plain dev browser falls back to typing. */
function pickFolderNative(): Promise<string | null> | null {
  const g = (
    window as unknown as {
      universe?: { pickFolder?: () => Promise<string | null> };
    }
  ).universe;
  return g?.pickFolder ? g.pickFolder() : null;
}

function GeneralTab() {
  const settings = useChat(s => s.settings);
  const saveSettings = useChat(s => s.saveSettings);
  const permissionMode = useChat(s => s.permissionMode);
  const switchPermissionMode = useChat(s => s.switchPermissionMode);
  const loaded = settings !== null;
  // Phase-3 report §2.4/§2.5 mirrors — every one saves immediately through
  // PUT /api/settings; local copies exist only so the control flips on
  // click, the store's refreshed settings re-sync them below
  const [behavior, setBehavior] = useState("steer");
  const [autoContinue, setAutoContinue] = useState(true);
  const [thinkLevel, setThinkLevel] = useState("balanced");
  const [groupFlags, setGroupFlags] = useState({
    explore: true,
    terminal: true,
    changes: true,
  });
  const [attachOn, setAttachOn] = useState(true);
  const [savingFlag, setSavingFlag] = useState<string | null>(null);
  const patchSettings = async (
    patch: Partial<Settings>,
    tag: string
  ): Promise<void> => {
    setSavingFlag(tag);
    try {
      await saveSettings(patch);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSavingFlag(null);
    }
  };
  // name: brand display + explicit edit mode (never an open text field)
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [savingName, setSavingName] = useState(false);
  // behavior
  const [lang, setLang] = useState("");
  const [savingLang, setSavingLang] = useState(false);
  // work folder
  const [root, setRoot] = useState("");
  const [savingRoot, setSavingRoot] = useState(false);

  useEffect(() => {
    if (!settings) return;
    setNameDraft(settings.assistant_name ?? "Universe");
    setLang(settings.response_language ?? "");
    // Phase-3 mirrors (skip a null behavior — it only means "not stored
    // yet" and must not blank the select back from what the user picked)
    if (settings.interaction_behavior)
      setBehavior(settings.interaction_behavior);
    setAutoContinue((settings.auto_continue_questions_s ?? 300) > 0);
    if (
      settings.default_thinking_level === "fast" ||
      settings.default_thinking_level === "balanced" ||
      settings.default_thinking_level === "max"
    )
      setThinkLevel(settings.default_thinking_level);
    setGroupFlags({
      explore: settings.group_explore_tools !== false,
      terminal: settings.group_terminal_tools !== false,
      changes: settings.group_change_tools !== false,
    });
    setAttachOn(settings.attachments_enabled !== false);
  }, [settings]);

  useEffect(() => {
    let alive = true;
    api
      .getSettings()
      .then(r => {
        if (alive) setRoot(r.project_root ?? "");
      })
      .catch(() => {
        /* offline: leave blank */
      });
    return () => {
      alive = false;
    };
  }, []);

  const commitName = async () => {
    const value = nameDraft.trim();
    if (!value) return;
    setSavingName(true);
    try {
      await saveSettings({ assistant_name: value });
      setEditingName(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSavingName(false);
    }
  };

  const saveLang = async () => {
    setSavingLang(true);
    try {
      await saveSettings({ response_language: lang });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSavingLang(false);
    }
  };

  const chooseFolder = async () => {
    try {
      const p = pickFolderNative();
      if (p === null) {
        // dev browser: the native dialog does not exist — the text field
        // below is the fallback, say so honestly instead of a dead button
        toast.error(
          "Folder dialog needs the desktop app — type the path below",
          {
            description: "Work folder",
          }
        );
        return;
      }
      const picked = await p;
      if (!picked) return; // cancelled
      setSavingRoot(true);
      try {
        await saveSettings({ project_root: picked });
        setRoot(picked);
        toast.success("Work folder set", { description: picked });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Save failed");
      } finally {
        setSavingRoot(false);
      }
    } catch {
      /* dialog unavailable */
    }
  };

  const saveRootTyped = async () => {
    setSavingRoot(true);
    try {
      await saveSettings({ project_root: root.trim() });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSavingRoot(false);
    }
  };

  const currentName = (settings?.assistant_name ?? "Universe").trim();

  return (
    <>
      <div className="sx-card">
        <h2>Assistant</h2>
        <p className="sx-sub">The identity Universe answers to.</p>
        {!loaded && (
          <p className="sx-muted" role="status">
            Loading settings from the bridge…
          </p>
        )}
        <div className="sx-idrow">
          {!editingName ? (
            <>
              <span className="sx-idname" aria-label="Assistant name">
                <span className="sx-idglyph" aria-hidden="true">
                  ✦
                </span>
                {currentName.toUpperCase()}
                <span className="sx-idsuffix">COMPUTER</span>
              </span>
              <button
                className="sx-btn"
                onClick={() => {
                  setNameDraft(currentName);
                  setEditingName(true);
                }}
                disabled={!loaded}
                aria-label="Edit assistant name"
              >
                <Pencil size={12} strokeWidth={1.9} /> Edit
              </button>
            </>
          ) : (
            <div className="sx-idedit">
              <input
                value={nameDraft}
                onChange={e => setNameDraft(e.target.value)}
                maxLength={40}
                autoFocus
                className="sx-input"
                aria-label="Assistant name"
              />
              <button
                className="sx-btn primary"
                disabled={
                  savingName ||
                  !nameDraft.trim() ||
                  nameDraft.trim() === currentName
                }
                onClick={() => void commitName()}
              >
                <Check size={12} strokeWidth={2.4} /> Save
              </button>
              <button
                className="sx-btn"
                disabled={savingName}
                onClick={() => {
                  setEditingName(false);
                  setNameDraft(currentName);
                }}
                aria-label="Cancel rename"
              >
                <X size={12} strokeWidth={2} /> Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="sx-card">
        <h2>Workspace</h2>
        <p className="sx-sub">
          The folder Universe works in — files it creates land under here.
        </p>
        <div className="sx-folderrow">
          <FolderOpen size={15} strokeWidth={1.7} className="sx-folderic" />
          <span className="sx-folderpath mono" title={root || undefined}>
            {root || "Not set — Universe falls back to your home folder"}
          </span>
          <button
            className="sx-btn"
            disabled={!loaded || savingRoot}
            onClick={() => void chooseFolder()}
          >
            Choose…
          </button>
        </div>
        <details className="sx-foldersettings">
          <summary>Type a path instead</summary>
          <div className="sx-prow">
            <input
              value={root}
              onChange={e => setRoot(e.target.value)}
              placeholder="D:\\work\\projects"
              aria-label="Work folder path"
              className="sx-input mono"
              disabled={!loaded}
            />
            <button
              className="sx-btn"
              disabled={!loaded || savingRoot}
              onClick={() => void saveRootTyped()}
            >
              {savingRoot ? "Saving…" : "Save"}
            </button>
          </div>
        </details>
      </div>

      <div className="sx-card">
        <h2>Behavior</h2>
        <p className="sx-sub">
          How Universe answers when you don&apos;t say otherwise.
        </p>
        <div className="sx-prow">
          <label className="sx-flabel" htmlFor="sx-resp-lang">
            Response language
          </label>
          <select
            id="sx-resp-lang"
            className="sx-input"
            value={lang}
            disabled={!loaded}
            onChange={e => setLang(e.target.value)}
          >
            <option value="">Auto — match the message</option>
            <option value="en">English</option>
            <option value="ar">العربية</option>
          </select>
          <button
            className="sx-btn"
            disabled={
              !loaded ||
              savingLang ||
              lang === (settings?.response_language ?? "")
            }
            onClick={() => void saveLang()}
          >
            {savingLang ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <div className="sx-card">
        <h2>Agent interaction</h2>
        <p className="sx-sub">
          What a message does while Universe is working (report §2.4), plus the
          gate every new message starts with.
        </p>
        <div className="sx-field">
          <span className="sx-flabel">Permission mode</span>
          <div className="sx-seg" role="group" aria-label="Permission mode">
            {(["plan", "ask", "edit", "edit", "full"] as const).map(m => (
              <button
                key={m}
                type="button"
                className={`sx-segbtn${permissionMode === m ? " on" : ""}`}
                aria-pressed={permissionMode === m}
                disabled={savingFlag === "mode"}
                onClick={() => {
                  setSavingFlag("mode");
                  void switchPermissionMode(m)
                    .then(ok => {
                      if (!ok)
                        toast.error("Could not switch the permission mode");
                    })
                    .finally(() => setSavingFlag(null));
                }}
              >
                {m === "plan"
                  ? "Plan mode"
                  : m === "ask"
                    ? "Ask before changes"
                    : m === "edit"
                      ? "Edit automatically"
                      : "Full access"}
              </button>
            ))}
          </div>
          <span className="sx-hint">
            The live gate behind the composer chip — Plan refuses writes until
            plan.submit approves them.
          </span>
        </div>
        <Field
          label="Interaction behavior"
          hint="Queue buffers follow-ups typed while a goal runs and sends them when it ends; Steer course-corrects the running goal at its next step."
        >
          <select
            className="sx-input"
            value={behavior}
            disabled={!loaded}
            aria-label="Interaction behavior"
            onChange={e => {
              setBehavior(e.target.value);
              void patchSettings(
                { interaction_behavior: e.target.value },
                "behavior"
              );
            }}
          >
            <option value="steer">
              Steer — course-correct the running goal
            </option>
            <option value="queue">Queue — run follow-ups after the goal</option>
          </select>
        </Field>
        <Switch
          label="Automatically continue questions"
          hint="An unanswered agent question continues on its own after 5 minutes; off keeps it waiting for you."
          checked={autoContinue}
          disabled={!loaded}
          onChange={next => {
            const cur = settings?.auto_continue_questions_s ?? 300;
            setAutoContinue(next);
            void patchSettings(
              { auto_continue_questions_s: next ? (cur > 0 ? cur : 300) : 0 },
              "autoc"
            );
          }}
        />
        <Field
          label="Default thinking level"
          hint="Seeds the composer's Max chip for new messages — the chip still wins per message."
        >
          <select
            className="sx-input"
            value={thinkLevel}
            disabled={!loaded}
            aria-label="Default thinking level"
            onChange={e => {
              setThinkLevel(e.target.value);
              void patchSettings(
                { default_thinking_level: e.target.value },
                "think"
              );
            }}
          >
            <option value="fast">Fast — fewer rounds per answer</option>
            <option value="balanced">Balanced</option>
            <option value="max">Max — the full token budget</option>
          </select>
        </Field>
      </div>

      <div className="sx-card">
        <h2>Chat view</h2>
        <p className="sx-sub">
          How consecutive tool calls fold into the stream (report §2.5) and
          whether the composer offers file attachments — all default on, each
          toggle saves immediately.
        </p>
        <Switch
          label="Group exploration tools"
          hint="Fold consecutive reads/searches into one Explore row."
          checked={groupFlags.explore}
          disabled={!loaded}
          onChange={next => {
            setGroupFlags(f => ({ ...f, explore: next }));
            void patchSettings({ group_explore_tools: next }, "gexp");
          }}
        />
        <Switch
          label="Group terminal commands"
          hint="Fold consecutive non-reading shell commands into one Terminal row."
          checked={groupFlags.terminal}
          disabled={!loaded}
          onChange={next => {
            setGroupFlags(f => ({ ...f, terminal: next }));
            void patchSettings({ group_terminal_tools: next }, "gterm");
          }}
        />
        <Switch
          label="Group file changes"
          hint="Fold consecutive Write/Edit calls into one Changes row."
          checked={groupFlags.changes}
          disabled={!loaded}
          onChange={next => {
            setGroupFlags(f => ({ ...f, changes: next }));
            void patchSettings({ group_change_tools: next }, "gchg");
          }}
        />
        <Switch
          label="Attachments"
          hint='The composer "+ Add context" button (v1: text file paths ride the message).'
          checked={attachOn}
          disabled={!loaded}
          onChange={next => {
            setAttachOn(next);
            void patchSettings({ attachments_enabled: next }, "attach");
          }}
        />
        <p className="sx-hint" style={{ marginTop: 12 }}>
          ZCode also offers Show reasoning / Show todos here — Universe&apos;s
          stream keeps both always on for now.
        </p>
      </div>

      <div className="sx-card">
        <h2>Onboard</h2>
        <p className="sx-sub">
          Reopen the first-run wizard to review the welcome choices (name, model
          provider, voice) — nothing is reset.
        </p>
        <Link href="/welcome?review=1" className="sx-btn">
          Reopen onboarding
        </Link>
      </div>

      <div className="sx-card">
        <h2>System</h2>
        <label className="sx-label">Theme</label>
        <select
          className="sx-input"
          value={settings?.theme ?? "dark"}
          onChange={e => saveSettings({ theme: e.target.value })}
        >
          <option value="system">System</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
        <label className="sx-label" style={{ marginTop: 12 }}>
          UI font size
        </label>
        <input
          className="sx-input"
          type="range"
          min={10}
          max={20}
          value={settings?.uiFontSize ?? 14}
          onChange={e => saveSettings({ uiFontSize: Number(e.target.value) })}
        />
        <label className="sx-label">Code font size</label>
        <input
          className="sx-input"
          type="range"
          min={10}
          max={20}
          value={settings?.fontSize ?? 13}
          onChange={e => saveSettings({ fontSize: Number(e.target.value) })}
        />
      </div>
      <div className="sx-card">
        <h2>Terminal</h2>
        <label className="sx-label">Shell</label>
        <select
          className="sx-input"
          value={settings?.integratedTerminalShell ?? "auto"}
          onChange={e =>
            saveSettings({ integratedTerminalShell: e.target.value })
          }
        >
          <option value="auto">Auto (Git Bash → cmd.exe)</option>
          <option value="cmd.exe">cmd.exe</option>
          <option value="Git Bash">Git Bash</option>
        </select>
        <input
          className="sx-input"
          placeholder="Profile"
          value={settings?.terminalProfile ?? ""}
          onChange={e => saveSettings({ terminalProfile: e.target.value })}
          style={{ marginTop: 8 }}
        />
        <input
          className="sx-input"
          placeholder="Font family"
          value={settings?.terminalFontFamily ?? ""}
          onChange={e => saveSettings({ terminalFontFamily: e.target.value })}
          style={{ marginTop: 8 }}
        />
      </div>
      <div className="sx-card">
        <h2>Egress — HTTP proxy</h2>
        <input
          className="sx-input"
          placeholder="http://proxy:8080 (leave blank for direct)"
          value={settings?.httpProxy ?? ""}
          onChange={e => saveSettings({ httpProxy: e.target.value })}
        />
        <input
          className="sx-input"
          placeholder="No proxy (e.g. localhost,127.0.0.1)"
          value={settings?.httpProxyNoProxy ?? ""}
          onChange={e => saveSettings({ httpProxyNoProxy: e.target.value })}
          style={{ marginTop: 8 }}
        />
        <input
          className="sx-input"
          placeholder="CA cert PEM path"
          value={settings?.httpProxyCaCertPath ?? ""}
          onChange={e => saveSettings({ httpProxyCaCertPath: e.target.value })}
          style={{ marginTop: 8 }}
        />
        <p className="sx-hint">Restart to take effect.</p>
      </div>
      <div className="sx-card">
        <h2>Updates & lifecycle</h2>
        <div className="sx-row">
          <span>Close to tray on Windows</span>
          <label className="sx-switch">
            <input
              type="checkbox"
              checked={!!settings?.closeToTrayOnWindows}
              onChange={e =>
                saveSettings({ closeToTrayOnWindows: e.target.checked })
              }
            />
            <span />
          </label>
        </div>
        <div className="sx-row">
          <span>Keep awake while running</span>
          <label className="sx-switch">
            <input
              type="checkbox"
              checked={!!settings?.keepAwakeWhileRunning}
              onChange={e =>
                saveSettings({ keepAwakeWhileRunning: e.target.checked })
              }
            />
            <span />
          </label>
        </div>
        <div className="sx-row">
          <span>Auto-download updates</span>
          <label className="sx-switch">
            <input
              type="checkbox"
              checked={!!settings?.autoDownloadAndInstallUpdates}
              onChange={e =>
                saveSettings({
                  autoDownloadAndInstallUpdates: e.target.checked,
                })
              }
            />
            <span />
          </label>
        </div>
        <div className="sx-row">
          <span>Wrap long lines</span>
          <label className="sx-switch">
            <input
              type="checkbox"
              checked={!!settings?.wrapLongLines}
              onChange={e => saveSettings({ wrapLongLines: e.target.checked })}
            />
            <span />
          </label>
        </div>
        <label className="sx-label" style={{ marginTop: 12 }}>
          Auto-archive tasks
        </label>
        <select
          className="sx-input"
          value={settings?.taskAutoArchive ?? "off"}
          onChange={e => saveSettings({ taskAutoArchive: e.target.value })}
        >
          <option value="off">Off</option>
          <option value="3">After 3 days</option>
          <option value="7">After 7 days</option>
          <option value="14">After 14 days</option>
          <option value="30">After 30 days</option>
        </select>
      </div>
      <div className="sx-card">
        <h2>Data directory</h2>
        <div className="sx-row">
          <span
            className="mono"
            style={{
              fontSize: 12,
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {settings?.dataBaseDir || "— default ~/.universe"}
          </span>
          <button
            className="sx-btn"
            onClick={async () => {
              const r = await (
                window as unknown as {
                  universe?: { pickFolder?: () => Promise<string | null> };
                }
              ).universe?.pickFolder?.();
              if (r) saveSettings({ dataBaseDir: r });
            }}
          >
            Browse…
          </button>
        </div>
        <p className="sx-hint">
          Restart required. Do not pick the install folder itself.
        </p>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* providers                                                           */
/* ------------------------------------------------------------------ */

interface InstalledProvider {
  name: string;
  base_url: string;
  key_count: number;
  key_aliases: string[];
  keys?: ProviderKeyInfo[];
}

function ProvidersTab() {
  const [catalog, setCatalog] = useState<CatalogProvider[]>([]);
  const [reachable, setReachable] = useState<Record<string, boolean>>({});
  const [installed, setInstalled] = useState<InstalledProvider[]>([]);
  const [serving, setServing] = useState("none");
  const [failed, setFailed] = useState(false);
  // catalog still in flight → skeleton rows (never an empty-looking page)
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [openName, setOpenName] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [fieldErr, setFieldErr] = useState<string | null>(null);
  // templated presets (Cloudflare/Azure): collected URL per provider name
  const [urlTmpl, setUrlTmpl] = useState<Record<string, string>>({});
  // per-key management: provider whose "add key" row is open + draft value
  const [keyAddFor, setKeyAddFor] = useState<string | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  // custom provider card
  const [customOpen, setCustomOpen] = useState(false);
  const [customName, setCustomName] = useState("");
  const [customBase, setCustomBase] = useState("");
  const [customKey, setCustomKey] = useState("");
  // custom endpoints may not implement /models — an optional Model ID is
  // the honest escape hatch (bridge: empty = use the first live model)
  const [customModel, setCustomModel] = useState("");
  const [customBusy, setCustomBusy] = useState(false);

  // DECOUPLED loads: the catalog (static, instant) and the installed list
  // used to be joined before ANY setState — the tab painted nothing until
  // the slower request finished, and with an empty catalog every group
  // renders null: a page that looked empty for seconds. Each fetch now
  // paints the moment it lands; load() still resolves when both settle so
  // post-install callers can await a full refresh.
  const load = () => {
    setFailed(false);
    const catP = api
      .getProviderCatalog()
      .then(cat => {
        setCatalog(cat.catalog);
        setReachable(cat.local_reachable ?? {});
      })
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
    const provP = api
      .getProviders()
      .then(prov => {
        setInstalled(prov.providers);
        setServing(prov.has_provider ? prov.currently_serving : "none");
      })
      .catch(() => setFailed(true));
    return Promise.allSettled([catP, provP]);
  };

  useEffect(() => {
    void load();
  }, []);

  const installedNames = useMemo(
    () => new Set(installed.map(p => p.name)),
    [installed]
  );

  const resolvedUrl = (c: CatalogProvider): string => {
    if (!c.url_fields?.length) return c.base_url;
    let url = c.base_url;
    const vals = urlTmpl[c.name] ?? "";
    c.url_fields.forEach((f, i) => {
      url = url.replace(`{${f}}`, vals.split(/[\s,]+/)[i] ?? "");
    });
    return url;
  };

  const install = async (c: CatalogProvider) => {
    const key = apiKey.trim();
    if (!key && !c.key_optional) {
      setFieldErr("Paste an API key first.");
      return;
    }
    let url = c.base_url;
    if (c.url_fields?.length) {
      const vals = (urlTmpl[c.name] ?? "").trim();
      const got = vals ? vals.split(/[\s,]+/).filter(Boolean).length : 0;
      if (got < c.url_fields.length) {
        setFieldErr(`Fill the URL value(s): ${c.url_fields.join(", ")}`);
        return;
      }
      url = resolvedUrl(c);
      if (url.includes("{")) {
        setFieldErr(`Fill the URL value(s): ${c.url_fields.join(", ")}`);
        return;
      }
    }
    setFieldErr(null);
    setBusy(c.name);
    try {
      const r = await api.installProvider({
        name: c.name,
        base_url: url,
        api_key: key,
      });
      toast.success(
        r.key_appended ? `${r.provider}: key added` : `${r.provider} verified`,
        {
          description: r.key_appended
            ? "more keys — Universe rotates them automatically"
            : `${r.agents_routed.length} agents on ${r.model}`,
        }
      );
      setApiKey("");
      setOpenName(null);
      useSetup.getState().markProvider();
      await load();
      // the fresh key changes the model pool the next probe sees
      invalidateModelsCache(c.name);
    } catch (e) {
      setFieldErr(e instanceof Error ? e.message : "Install failed");
    } finally {
      setBusy(null);
    }
  };

  const installCustom = async () => {
    const nm = customName.trim();
    const base = customBase.trim();
    const key = customKey.trim();
    if (!nm || !base || !key) {
      setFieldErr("Custom provider needs a name, base URL and API key.");
      return;
    }
    setFieldErr(null);
    setCustomBusy(true);
    try {
      const r = await api.installProvider({
        name: nm,
        base_url: base,
        api_key: key,
        ...(customModel.trim() ? { defaultModel: customModel.trim() } : {}),
      });
      toast.success(`${r.provider} verified`, {
        description: `${r.agents_routed.length} agents on ${r.model}`,
      });
      setCustomOpen(false);
      setCustomName("");
      setCustomBase("");
      setCustomKey("");
      setCustomModel("");
      useSetup.getState().markProvider();
      await load();
    } catch (e) {
      setFieldErr(
        e instanceof BridgeError ? e.message : "Could not reach that endpoint"
      );
    } finally {
      setCustomBusy(false);
    }
  };

  const addKey = async (name: string) => {
    const key = keyDraft.trim();
    if (!key) return;
    setBusy(name);
    try {
      await api.addProviderKey(name, key);
      toast.success(`Key added to ${name}`, {
        description: "Universe rotates it with the others automatically",
      });
      setKeyDraft("");
      setKeyAddFor(null);
      await load();
      invalidateModelsCache(name);
    } catch (e) {
      toast.error(
        e instanceof BridgeError ? e.message : "key rejected by the provider",
        { description: "Nothing was stored" }
      );
    } finally {
      setBusy(null);
    }
  };

  const removeKey = async (name: string, keyId: number, alias: string) => {
    askConfirm({
      title: `Remove ${alias} from ${name}?`,
      body: "The key is deleted; rotation continues with the rest.",
      confirmLabel: "Remove key",
      danger: true,
      onConfirm: () =>
        void (async () => {
          try {
            await api.removeProviderKey(name, keyId);
            toast.success(`Key removed from ${name}`);
            await load();
          } catch (e) {
            toast.error(
              e instanceof BridgeError ? e.message : "could not remove key"
            );
          }
        })(),
    });
  };

  const remove = async (name: string) => {
    askConfirm({
      title: `Remove ${name}?`,
      body: "Its keys are deleted and agents fall back to remaining providers.",
      confirmLabel: "Remove",
      danger: true,
      onConfirm: () => void doRemove(name),
    });
  };

  const doRemove = async (name: string) => {
    setBusy(name);
    try {
      await api.removeProvider(name);
      toast.success(`${name} removed`);
      invalidateModelsCache(name);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Remove failed");
    } finally {
      setBusy(null);
    }
  };

  const groups: Array<{
    key: string;
    title: string;
    hint: string;
    items: CatalogProvider[];
  }> = [
    {
      key: "free",
      title: "Free-tier cloud",
      hint: "A real key from a genuinely free tier.",
      items: catalog.filter(c => c.category === "free"),
    },
    {
      key: "paid",
      title: "Paid cloud",
      hint: "Pay-per-token; spend is controlled on the provider side.",
      items: catalog.filter(c => c.category === "paid"),
    },
    {
      key: "local",
      title: "Local servers",
      hint: "Runs on this machine — no key, no internet.",
      items: catalog.filter(c => c.category === "local"),
    },
  ];

  return (
    <div className="sx-card">
      <h2>Providers</h2>
      <p className="sx-sub">
        Serving now: <b>{serving}</b>. A verified key routes every agent to it.
      </p>
      {failed && (
        <div className="sx-err" role="alert">
          <span>The provider list couldn&apos;t load.</span>
          <button className="sx-btn" onClick={() => void load()}>
            Retry
          </button>
        </div>
      )}
      {/* catalog still in flight: skeleton cards — the page never looks empty */}
      {loading && catalog.length === 0 && (
        <div className="sx-group" role="status" aria-label="Loading providers">
          <div className="sx-gtitle">Loading providers…</div>
          <div className="sx-grid">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="sx-prov sx-skel">
                <span className="sx-skel-line" style={{ width: "55%" }} />
                <span className="sx-skel-line" style={{ width: "80%" }} />
                <span className="sx-skel-btn" />
              </div>
            ))}
          </div>
        </div>
      )}
      {!loading &&
        groups.map(g =>
          g.items.length ? (
            <div key={g.key} className="sx-group">
              <div className="sx-gtitle">{g.title}</div>
              <p className="sx-ghint">{g.hint}</p>
              <div className="sx-grid">
                {g.items.map(c => {
                  const isInstalled = installedNames.has(c.name);
                  const isOpen = openName === c.name;
                  const isLocal = g.key === "local";
                  const live = reachable[c.name] === true;
                  return (
                    <div
                      key={c.name}
                      className={`sx-prov${isInstalled ? " installed" : ""}`}
                    >
                      <div className="sx-pname">
                        {c.name}
                        {isInstalled && (
                          <span className="sx-pill serving">
                            installed
                            {(installed.find(p => p.name === c.name)
                              ?.key_count ?? 1) > 1 &&
                              ` · ${installed.find(p => p.name === c.name)!.key_count} keys`}
                          </span>
                        )}
                        {serving === c.name && (
                          <span className="sx-pill serving">serving</span>
                        )}
                        {isLocal && (
                          <span
                            className={`sx-pill${live ? " serving" : " offline"}`}
                          >
                            {live && (
                              <span className="sx-dot" aria-hidden="true" />
                            )}
                            {live ? "online" : "offline"}
                          </span>
                        )}
                      </div>
                      {c.note && <p className="sx-pnote">{c.note}</p>}
                      {isInstalled ? (
                        <div>
                          <div className="sx-prow">
                            <button
                              className="sx-link"
                              onClick={() => {
                                setKeyAddFor(
                                  keyAddFor === c.name ? null : c.name
                                );
                                setKeyDraft("");
                              }}
                            >
                              + Add key
                            </button>
                            <button
                              className="sx-btn danger"
                              disabled={busy !== null}
                              onClick={() => void remove(c.name)}
                              aria-label={`Remove ${c.name}`}
                            >
                              {busy === c.name ? (
                                <LoaderCircle size={12} className="af-spin" />
                              ) : (
                                <Trash2 size={12} />
                              )}
                            </button>
                          </div>
                          {/* key inventory (masked — the secret never leaves the bridge) */}
                          {(
                            installed.find(p => p.name === c.name)?.keys ?? []
                          ).map(k => (
                            <div key={k.id} className="sx-keyrow">
                              <span className="sx-keyalias mono">
                                {k.alias}
                              </span>
                              <span className="sx-keyprev mono">
                                {k.preview}
                              </span>
                              {k.disabled && (
                                <span
                                  className="sx-pill bad"
                                  title="recent error burst — still kept as a fallback"
                                >
                                  degraded
                                </span>
                              )}
                              <span className="flex-sp" />
                              <button
                                className="sx-keydel"
                                onClick={() =>
                                  void removeKey(c.name, k.id, k.alias)
                                }
                                aria-label={`Remove ${k.alias}`}
                                title="Remove this key"
                              >
                                <X size={11} strokeWidth={2} />
                              </button>
                            </div>
                          ))}
                          {keyAddFor === c.name && (
                            <div className="sx-keyadd">
                              <input
                                type="password"
                                autoFocus
                                value={keyDraft}
                                onChange={e => setKeyDraft(e.target.value)}
                                onKeyDown={e =>
                                  e.key === "Enter" && void addKey(c.name)
                                }
                                placeholder="paste another API key…"
                                aria-label={`Add key to ${c.name}`}
                                className="sx-input mono"
                              />
                              <button
                                className="sx-btn primary"
                                disabled={busy !== null || !keyDraft.trim()}
                                onClick={() => void addKey(c.name)}
                              >
                                {busy === c.name ? (
                                  <LoaderCircle size={12} className="af-spin" />
                                ) : (
                                  <Check size={12} strokeWidth={2.4} />
                                )}
                                Verify
                              </button>
                            </div>
                          )}
                          <p className="sx-ghint">
                            Keys are managed here — the model itself is picked
                            from the chip inside the chat composer.
                          </p>
                        </div>
                      ) : !isOpen ? (
                        <div className="sx-prow">
                          {c.key_optional ? (
                            <button
                              className="sx-btn primary"
                              disabled={busy !== null}
                              onClick={() => void install(c)}
                            >
                              {busy === c.name ? "Verifying…" : "Connect"}
                            </button>
                          ) : (
                            <button
                              className="sx-btn"
                              onClick={() => {
                                setOpenName(c.name);
                                setFieldErr(null);
                              }}
                            >
                              Add API key
                            </button>
                          )}
                          {c.key_url && (
                            <button
                              className="sx-link"
                              onClick={() => {
                                void window.open(
                                  c.key_url,
                                  "_blank",
                                  "noopener"
                                );
                              }}
                            >
                              {g.key === "free"
                                ? "Get a free key"
                                : "Get a key"}
                            </button>
                          )}
                        </div>
                      ) : (
                        <div>
                          <Field label={`${c.name} API key`}>
                            <input
                              type="password"
                              value={apiKey}
                              onChange={e => setApiKey(e.target.value)}
                              placeholder="paste the key…"
                              className="sx-input mono"
                              aria-label={`${c.name} API key`}
                              autoFocus
                            />
                          </Field>
                          {c.url_fields?.length ? (
                            <Field
                              label={`URL value — ${c.url_fields.join(", ")}`}
                              hint="Fill the placeholder(s) in the base URL, in order (space-separated)."
                            >
                              <input
                                value={urlTmpl[c.name] ?? ""}
                                onChange={e =>
                                  setUrlTmpl(p => ({
                                    ...p,
                                    [c.name]: e.target.value,
                                  }))
                                }
                                placeholder={c.url_fields.join(" ")}
                                className="sx-input mono"
                                aria-label={`URL values for ${c.name}`}
                              />
                            </Field>
                          ) : null}
                          {/* no model field here: the bridge routes agents to
                            the first LIVE model the key's probe discovers —
                            picking a model at install time was noise */}
                          {fieldErr && (
                            <p className="sx-ferr" role="alert">
                              {fieldErr}
                            </p>
                          )}
                          <div className="sx-prow">
                            <button
                              className="sx-btn primary"
                              disabled={busy !== null}
                              onClick={() => void install(c)}
                            >
                              {busy === c.name ? (
                                <LoaderCircle size={12} className="af-spin" />
                              ) : (
                                <Check size={12} strokeWidth={2.4} />
                              )}
                              Verify & install
                            </button>
                            <button
                              className="sx-link"
                              disabled={busy !== null}
                              onClick={() => {
                                setOpenName(null);
                                setFieldErr(null);
                              }}
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null
        )}

      {/* ---- custom OpenAI-compatible endpoint ---- */}
      <div className="sx-group">
        <div className="sx-gtitle">Custom endpoint</div>
        <p className="sx-ghint">
          Any OpenAI-compatible server — point Universe at it with a name, base
          URL and key.
        </p>
        {!customOpen ? (
          <button className="sx-btn" onClick={() => setCustomOpen(true)}>
            <Plus size={12} strokeWidth={2} /> Add custom provider
          </button>
        ) : (
          <div className="sx-custom">
            <Field label="Name">
              <input
                value={customName}
                onChange={e => setCustomName(e.target.value)}
                placeholder="My vLLM box"
                className="sx-input"
                aria-label="Custom provider name"
                maxLength={60}
              />
            </Field>
            <Field label="Base URL">
              <input
                value={customBase}
                onChange={e => setCustomBase(e.target.value)}
                placeholder="http://192.168.1.10:8000/v1"
                className="sx-input mono"
                aria-label="Custom provider base URL"
              />
            </Field>
            <Field label="API key">
              <input
                type="password"
                value={customKey}
                onChange={e => setCustomKey(e.target.value)}
                placeholder="paste the key…"
                className="sx-input mono"
                aria-label="Custom provider API key"
                autoComplete="off"
              />
            </Field>
            <Field
              label="Model ID (optional)"
              hint="Leave empty if the endpoint lists its own models (/models); set it for servers that don't."
            >
              <input
                value={customModel}
                onChange={e => setCustomModel(e.target.value)}
                placeholder="auto — discovered live"
                className="sx-input mono"
                aria-label="Custom provider model ID"
              />
            </Field>
            {fieldErr && customOpen && (
              <p className="sx-ferr" role="alert">
                {fieldErr}
              </p>
            )}
            <div className="sx-prow">
              <button
                className="sx-btn primary"
                disabled={customBusy}
                onClick={() => void installCustom()}
              >
                {customBusy ? (
                  <LoaderCircle size={12} className="af-spin" />
                ) : (
                  <Check size={12} strokeWidth={2.4} />
                )}
                Verify & add
              </button>
              <button
                className="sx-link"
                disabled={customBusy}
                onClick={() => {
                  setCustomOpen(false);
                  setFieldErr(null);
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* voice                                                               */
/* ------------------------------------------------------------------ */

function VoiceTab() {
  const settings = useChat(s => s.settings);
  const saveSettings = useChat(s => s.saveSettings);
  const [enabled, setEnabled] = useState(false);
  const [wake, setWake] = useState("");
  const [stt, setStt] = useState({
    provider: "local",
    api_key: "",
    model: "",
    cloud_model: "",
    language: "",
  });
  const [tts, setTts] = useState({
    provider: "edge-tts",
    api_key: "",
    voice: "",
    model: "",
  });
  const [checking, setChecking] = useState(false);
  const [fieldErr, setFieldErr] = useState<string | null>(null);
  const loaded = settings !== null;

  useEffect(() => {
    if (!settings) return;
    setEnabled(!!settings.voice_enabled);
    setWake(settings.wake_word ?? "");
    setStt({
      provider: settings.stt_provider || "local",
      api_key: "",
      model: settings.stt_model || "",
      cloud_model: settings.stt_cloud_model || "",
      language: settings.stt_language ?? "",
    });
    setTts({
      provider: settings.tts_provider || "edge-tts",
      api_key: "",
      voice: settings.tts_voice || "",
      model: settings.tts_model || "",
    });
  }, [settings]);

  // the bridge returns keys MASKED (never plaintext over HTTP) — a masked
  // echo on save means "unchanged". Show what is stored instead of a
  // mysteriously empty field that made the wizard-entered keys look lost.
  const sttKeySaved = !!settings?.stt_api_key && settings.stt_api_key !== "";
  const ttsKeySaved = !!settings?.tts_api_key && settings.tts_api_key !== "";

  // per-provider voice options (mirror voice.py's real semantics:
  // edge = name, openai = built-in voice, elevenlabs = voice id,
  // fish = model/reference id). Free-text stays possible via datalist.
  const EDGE_VOICES = [
    "en-US-AriaNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "ar-SA-ZariyahNeural",
    "ar-SA-HamedNeural",
    "ar-AE-FatimaNeural",
    "fr-FR-DeniseNeural",
    "de-DE-KatjaNeural",
    "ja-JP-NanamiNeural",
    "ko-KR-SunHiNeural",
    "zh-CN-XiaoxiaoNeural",
  ];
  const OPENAI_VOICES = [
    "alloy",
    "echo",
    "fable",
    "onyx",
    "nova",
    "shimmer",
    "sage",
    "coral",
  ];
  const ELEVEN_VOICES = [
    "21m00Tcm4TlvDq8ikWAM", // Rachel
    "2E8WUyDxNvq16Phn5PQF", // Drew
    "9BWtsMINqrJLrR4Cw6tP", // Ari
    "EXAVITQu4vr4xnSDxMaL", // Sarah
  ];
  const STT_LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v3"];
  const STT_CLOUD_MODELS: Record<string, string[]> = {
    openai: ["whisper-1", "gpt-4o-mini-transcribe", "gpt-4o-transcribe"],
    groq: [
      "whisper-large-v3",
      "whisper-large-v3-turbo",
      "playai-cc/llamaguard-4-12b-audio",
    ],
  };
  const STT_LANGS: Array<[string, string]> = [
    ["", "Auto — detect"],
    ["ar", "العربية"],
    ["en", "English"],
    ["fr", "Français"],
    ["de", "Deutsch"],
    ["es", "Español"],
    ["tr", "Türkçe"],
    ["ru", "Русский"],
    ["zh", "中文"],
    ["ja", "日本語"],
    ["ko", "한국어"],
  ];

  const save = async () => {
    setFieldErr(null);
    if (stt.provider !== "local" && stt.api_key) {
      setChecking(true);
      try {
        const r = await api.checkVoiceKey("stt", stt.provider, stt.api_key);
        if (!r.ok) {
          setFieldErr(`STT key rejected${r.error ? `: ${r.error}` : ""}`);
          return;
        }
      } finally {
        setChecking(false);
      }
    }
    if (tts.provider !== "edge-tts" && tts.api_key) {
      setChecking(true);
      try {
        const r = await api.checkVoiceKey("tts", tts.provider, tts.api_key);
        if (!r.ok) {
          setFieldErr(`TTS key rejected${r.error ? `: ${r.error}` : ""}`);
          return;
        }
      } finally {
        setChecking(false);
      }
    }
    try {
      await saveSettings({
        voice_enabled: enabled,
        wake_word: wake.trim() || "wake up Universe",
        stt_provider: stt.provider,
        ...(stt.api_key ? { stt_api_key: stt.api_key } : {}),
        stt_model: stt.model,
        stt_cloud_model: stt.cloud_model,
        stt_language: stt.language,
        tts_provider: tts.provider,
        ...(tts.api_key ? { tts_api_key: tts.api_key } : {}),
        tts_voice: tts.voice,
        tts_model: tts.model,
      } as Partial<Settings>);
      setStt(s => ({ ...s, api_key: "" }));
      setTts(s => ({ ...s, api_key: "" }));
    } catch (e) {
      setFieldErr(e instanceof Error ? e.message : "Save failed");
    }
  };

  return (
    <div className="sx-card">
      <h2>Voice</h2>
      <p className="sx-sub">
        Local by default. Cloud needs your own key — checked live before saving.
      </p>
      {!loaded && (
        <p className="sx-muted" role="status">
          Loading settings from the bridge…
        </p>
      )}
      <label className="sx-toggle">
        <input
          type="checkbox"
          checked={enabled}
          disabled={!loaded}
          onChange={e => setEnabled(e.target.checked)}
        />
        <span>Voice channel enabled</span>
      </label>
      <Field
        label="Wake word"
        hint="Say this to wake the voice channel (lives here — it only matters when voice is on)."
      >
        <input
          value={wake}
          onChange={e => setWake(e.target.value)}
          placeholder="wake up Universe"
          aria-label="Wake word"
          maxLength={60}
          className="sx-input"
          disabled={!loaded}
        />
      </Field>
      <div className="sx-grid2">
        <div>
          <div className="sx-gtitle">Speech to text</div>
          <div className="sx-prow" role="group" aria-label="STT provider">
            {STT_PROVIDERS.map(p => (
              <button
                key={p}
                className={`sx-btn${stt.provider === p ? " primary" : ""}`}
                onClick={() => setStt({ ...stt, provider: p })}
              >
                {p}
              </button>
            ))}
          </div>
          <Field
            label="STT key"
            hint={
              stt.provider === "local"
                ? "Local needs no key."
                : sttKeySaved && !stt.api_key
                  ? `Key saved (${settings?.stt_api_key}) — paste a new one to replace it.`
                  : "Checked live before saving."
            }
          >
            <input
              type="password"
              value={stt.api_key}
              disabled={!loaded || stt.provider === "local"}
              onChange={e => setStt({ ...stt, api_key: e.target.value })}
              placeholder={sttKeySaved ? "•••••• (saved)" : "key"}
              aria-label="STT API key"
              autoComplete="off"
              className="sx-input mono"
            />
          </Field>
          <Field label="Local model" hint="faster-whisper model size.">
            <input
              list="sx-stt-local-models"
              value={stt.model}
              disabled={!loaded}
              onChange={e => setStt({ ...stt, model: e.target.value })}
              placeholder="base"
              aria-label="STT local model"
              className="sx-input mono"
            />
            <datalist id="sx-stt-local-models">
              {STT_LOCAL_MODELS.map(m => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </Field>
          <Field label="Cloud model" hint="Groq / OpenAI speech model.">
            <input
              list="sx-stt-cloud-models"
              value={stt.cloud_model}
              disabled={!loaded}
              onChange={e => setStt({ ...stt, cloud_model: e.target.value })}
              placeholder="whisper-large-v3"
              aria-label="STT cloud model"
              className="sx-input mono"
            />
            <datalist id="sx-stt-cloud-models">
              {(STT_CLOUD_MODELS[stt.provider] ?? []).map(m => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </Field>
          <Field label="Language" hint="Empty = auto.">
            <select
              value={stt.language}
              disabled={!loaded}
              onChange={e => setStt({ ...stt, language: e.target.value })}
              aria-label="STT language"
              className="sx-input"
            >
              {STT_LANGS.map(([v, l]) => (
                <option key={v || "edit"} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div>
          <div className="sx-gtitle">Text to speech</div>
          <div className="sx-prow" role="group" aria-label="TTS provider">
            {TTS_PROVIDERS.map(p => (
              <button
                key={p}
                className={`sx-btn${tts.provider === p ? " primary" : ""}`}
                onClick={() => setTts({ ...tts, provider: p })}
              >
                {p}
              </button>
            ))}
          </div>
          <Field
            label="TTS key"
            hint={
              tts.provider === "edge-tts"
                ? "Free, no key."
                : ttsKeySaved && !tts.api_key
                  ? `Key saved (${settings?.tts_api_key}) — paste a new one to replace it.`
                  : "Checked live before saving."
            }
          >
            <input
              type="password"
              value={tts.api_key}
              disabled={!loaded || tts.provider === "edge-tts"}
              onChange={e => setTts({ ...tts, api_key: e.target.value })}
              placeholder={ttsKeySaved ? "•••••• (saved)" : "key"}
              aria-label="TTS API key"
              autoComplete="off"
              className="sx-input mono"
            />
          </Field>
          <Field
            label="Voice"
            hint={
              tts.provider === "elevenlabs"
                ? "ElevenLabs voice ID."
                : tts.provider === "fish-audio"
                  ? "Fish Audio model/reference id."
                  : "Pick or type a voice."
            }
          >
            <input
              list="sx-tts-voices"
              value={tts.voice}
              disabled={!loaded}
              onChange={e => setTts({ ...tts, voice: e.target.value })}
              placeholder="en-US-AriaNeural"
              aria-label="TTS voice"
              className="sx-input mono"
            />
            <datalist id="sx-tts-voices">
              {(tts.provider === "edge-tts"
                ? EDGE_VOICES
                : tts.provider === "openai"
                  ? OPENAI_VOICES
                  : tts.provider === "elevenlabs"
                    ? ELEVEN_VOICES
                    : []
              ).map(v => (
                <option key={v} value={v} />
              ))}
            </datalist>
          </Field>
          <Field label="Model" hint="Cloud TTS model override.">
            <input
              list="sx-tts-models"
              value={tts.model}
              disabled={!loaded}
              onChange={e => setTts({ ...tts, model: e.target.value })}
              placeholder="tts-1"
              aria-label="TTS model"
              className="sx-input mono"
            />
            <datalist id="sx-tts-models">
              {(tts.provider === "openai"
                ? ["gpt-4o-mini-tts", "tts-1", "tts-1-hd"]
                : tts.provider === "elevenlabs"
                  ? ["eleven_multilingual_v2", "eleven_turbo_v2_5"]
                  : tts.provider === "fish-audio"
                    ? ["s2.1-pro", "s2-pro"]
                    : []
              ).map(m => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </Field>
        </div>
      </div>
      {fieldErr && (
        <p className="sx-ferr" role="alert">
          {fieldErr}
        </p>
      )}
      <div className="sx-prow">
        <button
          className="sx-btn primary"
          disabled={!loaded || checking}
          onClick={() => void save()}
        >
          {checking ? "Checking keys…" : "Save voice settings"}
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* agents                                                              */
/* ------------------------------------------------------------------ */

function SubagentsTab() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [failed, setFailed] = useState(false);

  const load = async () => {
    setFailed(false);
    try {
      const ag = await api.listAgents();
      setAgents(ag.agents);
    } catch {
      setFailed(true);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  return (
    <div className="sx-card">
      <h2>Subagents</h2>
      <p className="sx-sub">
        Specialists the main agent can spawn mid-goal (research, code, review,
        security, design). They inherit the one model set from the chat composer
        — there is no per-subagent model.
      </p>
      {failed && (
        <div className="sx-err" role="alert">
          <span>The subagent roster couldn't load.</span>
          <button className="sx-btn" onClick={() => void load()}>
            Retry
          </button>
        </div>
      )}
      {agents.map(a => (
        <div key={a.name} className="sx-row" style={{ flexWrap: "wrap" }}>
          <div className="sx-pname">
            {a.name}
            <span className="sx-pill">{a.tier}</span>
          </div>
          <span className="sx-rowmeta">{a.specialty}</span>
          <p
            className="sx-tools"
            style={{ flexBasis: "100%", marginBottom: 0 }}
          >
            tools: {a.tools.slice(0, 10).join(", ")}
            {a.tools.length > 10 ? ` +${a.tools.length - 10}` : ""}
          </p>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* mcp                                                                 */
/* ------------------------------------------------------------------ */

function parseHeaders(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    const i = t.indexOf("=");
    if (i <= 0) throw new Error(`Bad header line: "${t}" (use KEY=VALUE)`);
    out[t.slice(0, i).trim()] = t.slice(i + 1).trim();
  }
  return out;
}

function McpTab() {
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [fieldErr, setFieldErr] = useState<string | null>(null);
  const [fname, setFname] = useState("");
  const [transport, setTransport] = useState<"stdio" | "http" | "sse">("stdio");
  const [command, setCommand] = useState("");
  const [url, setUrl] = useState("");
  const [headers, setHeaders] = useState("");
  const [hosts, setHosts] = useState("");
  const [readOnly, setReadOnly] = useState(true);

  const load = async () => {
    setFailed(false);
    try {
      const r = await api.listMcpServers();
      setServers(r.servers);
    } catch {
      setFailed(true);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const resetForm = () => {
    setFname("");
    setTransport("stdio");
    setCommand("");
    setUrl("");
    setHeaders("");
    setHosts("");
    setReadOnly(true);
    setFieldErr(null);
  };

  const add = async () => {
    const name = fname.trim();
    if (!name) {
      setFieldErr("Give the server a name.");
      return;
    }
    let hdrs: Record<string, string>;
    try {
      hdrs = parseHeaders(headers);
    } catch (e) {
      setFieldErr(e instanceof Error ? e.message : "Bad headers");
      return;
    }
    setFieldErr(null);
    setBusy(name);
    try {
      await api.addMcpServer({
        name,
        transport,
        ...(transport === "stdio"
          ? { command: command.trim() }
          : { url: url.trim() }),
        ...(Object.keys(hdrs).length ? { headers: hdrs } : {}),
        ...(hosts.trim()
          ? {
              declared_hosts: hosts
                .split(",")
                .map(s => s.trim())
                .filter(Boolean),
            }
          : {}),
        read_only: readOnly,
      });
      toast.success(`${name} added and verified`);
      resetForm();
      setAdding(false);
      await load();
    } catch (e) {
      setFieldErr(e instanceof Error ? e.message : "Add failed");
    } finally {
      setBusy(null);
    }
  };

  const startEdit = (s: McpServerInfo) => {
    setEditing(s.name);
    setAdding(false);
    setFname(s.name);
    setTransport(s.transport);
    setCommand(s.command);
    setUrl(s.url);
    setHeaders(
      Object.entries(
        (s as unknown as { headers?: Record<string, string> }).headers ?? {}
      )
        .map(([k, v]) => `${k}=${v}`)
        .join("\n")
    );
    setHosts((s.declared_hosts ?? []).join(", "));
    setReadOnly(s.read_only);
    setFieldErr(null);
  };

  const saveEdit = async () => {
    if (!editing) return;
    let hdrs: Record<string, string>;
    try {
      hdrs = parseHeaders(headers);
    } catch (e) {
      setFieldErr(e instanceof Error ? e.message : "Bad headers");
      return;
    }
    setFieldErr(null);
    setBusy(editing);
    try {
      await api.patchMcpServer(editing, {
        transport,
        ...(transport === "stdio"
          ? { command: command.trim() }
          : { url: url.trim() }),
        headers: hdrs,
        declared_hosts: hosts
          .split(",")
          .map(s => s.trim())
          .filter(Boolean),
        read_only: readOnly,
      });
      toast.success(`${editing} updated`);
      setEditing(null);
      resetForm();
      await load();
    } catch (e) {
      setFieldErr(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(null);
    }
  };

  const toggle = async (s: McpServerInfo) => {
    setBusy(s.name);
    try {
      await api.patchMcpServer(s.name, { enabled: !s.enabled });
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Toggle failed");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (name: string) => {
    askConfirm({
      title: `Remove MCP server "${name}"?`,
      body: "The server is stopped and its configuration deleted.",
      confirmLabel: "Remove",
      danger: true,
      onConfirm: () => void doRemove(name),
    });
  };

  const doRemove = async (name: string) => {
    setBusy(name);
    try {
      await api.removeMcpServer(name);
      toast.success(`${name} removed`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Remove failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="sx-card">
      <h2>MCP servers</h2>
      <p className="sx-sub">
        stdio (local command), http (Streamable-HTTP) or sse — verified with a
        real handshake before saving.
      </p>
      {failed && (
        <div className="sx-err" role="alert">
          <span>The server list couldn&apos;t load.</span>
          <button className="sx-btn" onClick={() => void load()}>
            Retry
          </button>
        </div>
      )}
      {servers.map(s => (
        <div key={s.name} className="sx-row" style={{ flexWrap: "wrap" }}>
          <div className="sx-pname">
            {s.name}
            <span className="sx-transport mono">{s.transport}</span>
            {s.handshake_ok ? (
              <span className="sx-pill serving">{s.tool_count} tools</span>
            ) : (
              <span className="sx-pill offline">no handshake</span>
            )}
            {!s.enabled && <span className="sx-pill offline">disabled</span>}
            {s.read_only && <span className="sx-pill">read-only</span>}
          </div>
          <button className="sx-link" onClick={() => void toggle(s)}>
            {s.enabled ? "Disable" : "Enable"}
          </button>
          <button
            className="sx-link"
            onClick={() =>
              editing === s.name ? setEditing(null) : startEdit(s)
            }
          >
            {editing === s.name ? "Close" : "Edit"}
          </button>
          <button
            className="sx-btn danger"
            disabled={busy !== null}
            onClick={() => void remove(s.name)}
            aria-label={`Remove ${s.name}`}
          >
            <Trash2 size={12} />
          </button>
          {editing === s.name && (
            <div style={{ flexBasis: "100%" }}>
              <Field
                label={transport === "stdio" ? "Command" : "URL"}
                hint={
                  transport === "stdio"
                    ? "Local command, e.g. npx -y @modelcontextprotocol/server-everything"
                    : "http = Streamable-HTTP, sse = SSE."
                }
              >
                {transport === "stdio" ? (
                  <input
                    value={command}
                    onChange={e => setCommand(e.target.value)}
                    className="sx-input mono"
                    aria-label="Server command"
                  />
                ) : (
                  <input
                    value={url}
                    onChange={e => setUrl(e.target.value)}
                    className="sx-input mono"
                    aria-label="Server URL"
                    inputMode="url"
                  />
                )}
              </Field>
              <Field label="Transport">
                <select
                  className="sx-input"
                  value={transport}
                  onChange={e =>
                    setTransport(e.target.value as "stdio" | "http" | "sse")
                  }
                  aria-label="Transport"
                >
                  <option value="stdio">stdio</option>
                  <option value="http">http</option>
                  <option value="sse">sse</option>
                </select>
              </Field>
              <div className="sx-prow">
                <button
                  className="sx-btn primary"
                  disabled={busy !== null}
                  onClick={() => void saveEdit()}
                >
                  {busy === editing ? "Saving…" : "Save changes"}
                </button>
              </div>
            </div>
          )}
        </div>
      ))}
      {!adding ? (
        <div className="sx-prow">
          <button
            className="sx-btn"
            onClick={() => {
              setAdding(true);
              setEditing(null);
              resetForm();
            }}
          >
            Add server
          </button>
        </div>
      ) : (
        <div>
          <Field label="Name">
            <input
              value={fname}
              onChange={e => setFname(e.target.value)}
              placeholder="my-tools"
              className="sx-input mono"
              aria-label="Server name"
              maxLength={60}
            />
          </Field>
          <Field label="Transport">
            <select
              className="sx-input"
              value={transport}
              onChange={e =>
                setTransport(e.target.value as "stdio" | "http" | "sse")
              }
              aria-label="Transport"
            >
              <option value="stdio">stdio — local command</option>
              <option value="http">http — Streamable-HTTP URL</option>
              <option value="sse">sse — SSE URL</option>
            </select>
          </Field>
          {transport === "stdio" ? (
            <Field label="Command">
              <input
                value={command}
                onChange={e => setCommand(e.target.value)}
                placeholder="npx -y @modelcontextprotocol/server-everything"
                className="sx-input mono"
                aria-label="Server command"
              />
            </Field>
          ) : (
            <Field label="URL">
              <input
                value={url}
                onChange={e => setUrl(e.target.value)}
                placeholder="http://127.0.0.1:8000/mcp"
                className="sx-input mono"
                aria-label="Server URL"
                inputMode="url"
              />
            </Field>
          )}
          <Field label="Headers" hint="One per line: KEY=VALUE.">
            <input
              value={headers}
              onChange={e => setHeaders(e.target.value)}
              placeholder="Authorization=Bearer EXAMPLE-TOKEN"
              className="sx-input mono"
              aria-label="Headers"
            />
          </Field>
          <Field
            label="Declared hosts"
            hint="Comma-separated egress allowlist."
          >
            <input
              value={hosts}
              onChange={e => setHosts(e.target.value)}
              placeholder="example.com"
              className="sx-input mono"
              aria-label="Declared hosts"
            />
          </Field>
          <label className="sx-toggle">
            <input
              type="checkbox"
              checked={readOnly}
              onChange={e => setReadOnly(e.target.checked)}
            />
            <span>Read-only</span>
          </label>
          {fieldErr && (
            <p className="sx-ferr" role="alert">
              {fieldErr}
            </p>
          )}
          <div className="sx-prow">
            <button
              className="sx-btn primary"
              disabled={busy !== null}
              onClick={() => void add()}
            >
              {busy ? "Verifying…" : "Add & verify"}
            </button>
            <button
              className="sx-link"
              onClick={() => {
                setAdding(false);
                resetForm();
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {fieldErr && !adding && (
        <p className="sx-ferr" role="alert">
          {fieldErr}
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* hooks (Phase 3 — the kill switch HookManager._load really reads)    */
/* ------------------------------------------------------------------ */

const HOOK_EVENTS = ["pre_tool", "pre_shell", "pre_write", "post_tool"];
const HOOK_ACTIONS = ["require_facts", "block"];

function HooksTab() {
  const settings = useChat(s => s.settings);
  const saveSettings = useChat(s => s.saveSettings);
  const loaded = settings !== null;
  const enabled = settings?.hooks_enabled !== false;
  const rawHooks = settings?.hooks;
  const rules: HookRule[] = Array.isArray(rawHooks) ? rawHooks : [];
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draftEvent, setDraftEvent] = useState("pre_shell");
  const [draftAction, setDraftAction] = useState("block");
  const [draftMatcher, setDraftMatcher] = useState("*");
  const [draftMessage, setDraftMessage] = useState("");

  const commit = async (patch: Partial<Settings>): Promise<boolean> => {
    setSaving(true);
    setErr(null);
    try {
      await saveSettings(patch);
      return true;
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
      return false;
    } finally {
      setSaving(false);
    }
  };

  const addRule = async () => {
    const rule: HookRule = {
      event: draftEvent,
      action: draftAction,
      matcher: draftMatcher.trim() || "*",
      message: draftMessage.trim() || undefined,
      enabled: true,
    };
    if (await commit({ hooks: [...rules, rule] })) {
      setAdding(false);
      setDraftMessage("");
      setDraftMatcher("*");
    }
  };

  return (
    <>
      <div className="sx-card">
        <h2>Hooks</h2>
        <p className="sx-sub">
          Declarative gates evaluated before every tool call — the FactGuard
          builtin (present the facts before the first shell command) plus your
          own rules below. The switch is the real kill switch: the bridge
          reloads its HookManager on save, so off means zero hooks fire until
          you turn it back on.
        </p>
        {!loaded && (
          <p className="sx-muted" role="status">
            Loading settings from the bridge…
          </p>
        )}
        <Switch
          label="Enable hooks"
          hint="Off lets every tool call pass without a hook check (builtins included)."
          checked={enabled}
          disabled={!loaded || saving}
          onChange={next => void commit({ hooks_enabled: next })}
        />
        {err && (
          <p className="sx-ferr" role="alert">
            {err}
          </p>
        )}
      </div>

      <div className="sx-card">
        <h2>Your rules</h2>
        <p className="sx-sub">
          desktop.hooks[] — matched per event before the call. post_tool is
          announce-only in v1 (it never blocks).
        </p>
        {rules.length === 0 && (
          <p className="sx-muted">
            No user rules yet — only the builtin FactGuard gate is active.
          </p>
        )}
        <div className="sx-hooklist">
          {rules.map((r, i) => (
            <div key={`${r.event}-${r.action}-${i}`} className="sx-hookrow">
              <span className="sx-hookmeta mono">
                {r.event} · {r.action} · {r.matcher || "*"}
              </span>
              {r.message && (
                <span className="sx-hookmsg" title={r.message}>
                  {r.message.slice(0, 120)}
                </span>
              )}
              <Switch
                label={`${r.event} ${r.action} rule`}
                checked={r.enabled !== false}
                disabled={!loaded || saving}
                onChange={next =>
                  void commit({
                    hooks: rules.map((x, j) =>
                      j === i ? { ...x, enabled: next } : x
                    ),
                  })
                }
              />
              <button
                type="button"
                className="sx-btn"
                aria-label={`Remove rule ${r.event} ${r.action}`}
                disabled={!loaded || saving}
                onClick={() =>
                  void commit({ hooks: rules.filter((_, j) => j !== i) })
                }
              >
                <Trash2 size={12} strokeWidth={1.9} />
              </button>
            </div>
          ))}
        </div>
        {!adding ? (
          <button
            type="button"
            className="sx-btn"
            disabled={!loaded || saving}
            onClick={() => setAdding(true)}
          >
            <Plus size={12} strokeWidth={2} /> Add rule
          </button>
        ) : (
          <div className="sx-hookadd">
            <div className="sx-prow">
              <Field label="Event">
                <select
                  className="sx-input"
                  value={draftEvent}
                  aria-label="Hook event"
                  onChange={e => setDraftEvent(e.target.value)}
                >
                  {HOOK_EVENTS.map(ev => (
                    <option key={ev} value={ev}>
                      {ev}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Action">
                <select
                  className="sx-input"
                  value={draftAction}
                  aria-label="Hook action"
                  onChange={e => setDraftAction(e.target.value)}
                >
                  {HOOK_ACTIONS.map(a => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field
              label="Matcher"
              hint='fnmatch over the tool name — "*" matches the whole event domain.'
            >
              <input
                className="sx-input mono"
                value={draftMatcher}
                placeholder="*"
                aria-label="Hook matcher"
                onChange={e => setDraftMatcher(e.target.value)}
              />
            </Field>
            <Field
              label="Message"
              hint="Shown when the hook stops the call (max 400 chars)."
            >
              <input
                className="sx-input"
                value={draftMessage}
                placeholder="Present the requested facts first"
                aria-label="Hook message"
                onChange={e => setDraftMessage(e.target.value)}
              />
            </Field>
            <div className="sx-prow">
              <button
                type="button"
                className="sx-btn primary"
                disabled={saving}
                onClick={() => void addRule()}
              >
                {saving ? "Saving…" : "Add rule"}
              </button>
              <button
                type="button"
                className="sx-link"
                onClick={() => setAdding(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* memory (live counts from universe.db — ids/names/counts, no secrets) */
/* ------------------------------------------------------------------ */

interface MemorySnap {
  counts?: {
    skills?: number;
    systems?: number;
    orbits?: number;
    trusted?: number;
  };
  stars?: unknown[];
  goals?: unknown[];
  generated_at?: number;
}

function MemoryTab() {
  const [snap, setSnap] = useState<MemorySnap | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    api
      .memory()
      .then(r => {
        if (alive) setSnap(r as MemorySnap);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  if (failed)
    return (
      <div className="sx-card">
        <h2>Memory</h2>
        <p className="sx-sub">
          The bridge did not answer — memory counts appear once it is back.
        </p>
      </div>
    );
  if (!snap)
    return (
      <div className="sx-card">
        <h2>Memory</h2>
        <p className="sx-muted" role="status">
          Loading the live snapshot…
        </p>
      </div>
    );
  const stats: Array<[string, number]> = [
    ["Skill solar systems", snap.counts?.systems ?? 0],
    ["Orbits", snap.counts?.orbits ?? 0],
    ["Skills", snap.counts?.skills ?? 0],
    ["Trusted skills", snap.counts?.trusted ?? 0],
    ["L3 stars", snap.stars?.length ?? 0],
    ["Goal planets", snap.goals?.length ?? 0],
  ];
  return (
    <div className="sx-card">
      <h2>Memory</h2>
      <p className="sx-sub">
        Live counts from <span className="mono">~/.universe</span> via GET
        /api/memory — ids, names and counts only, never secrets.
      </p>
      <div className="sx-statgrid">
        {stats.map(([label, n]) => (
          <div className="sx-stat" key={label}>
            <b>{n.toLocaleString()}</b>
            <span>{label}</span>
          </div>
        ))}
      </div>
      <p className="sx-hint" style={{ marginTop: 14 }}>
        Persistent memory (L2 episodic asteroids, L3 semantic stars) builds
        itself from finished goals — there is nothing to configure here yet.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* honest placeholder: the section exists in the report; its controls  */
/* arrive with a later phase — never a dead switch                     */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* skills (Phase C — the real L4 corpus)                               */
/* ------------------------------------------------------------------ */

function SkillsTab() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [cat, setCat] = useState("");
  const [skills, setSkills] = useState<SkillInfo[] | null>(null);
  const [cats, setCats] = useState<Array<{ category: string; n: number }>>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  const load = useCallback(async () => {
    setSkills(null);
    try {
      const r = await api.listSkills(debouncedQ, cat);
      setSkills(r.skills);
      setCats(r.categories);
      setErr(null);
    } catch (e) {
      setErr(
        e instanceof BridgeError
          ? e.message
          : "Universe bridge is not responding"
      );
    }
  }, [debouncedQ, cat]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (row: SkillInfo, next: boolean) => {
    // optimistic; the toggle is the only writable field in v1
    setSkills(
      rs => rs?.map(r => (r.id === row.id ? { ...r, enabled: next } : r)) ?? rs
    );
    try {
      await api.patchSkill(row.id, next);
    } catch (e) {
      setSkills(
        rs =>
          rs?.map(r => (r.id === row.id ? { ...r, enabled: !next } : r)) ?? rs
      );
      toast.error("Could not update the skill", {
        description:
          e instanceof BridgeError ? e.message : "bridge unreachable",
      });
    }
  };

  return (
    <div>
      <p className="sx-sub">
        The live skill corpus the agents search (L4).{" "}
        <b>A switched-off skill stays stored but never reaches an agent.</b>
      </p>
      <div className="sx-card">
        <div className="sk-controls">
          <input
            className="sx-input"
            value={q}
            placeholder="Search skills…"
            aria-label="Search skills"
            onChange={e => setQ(e.target.value)}
          />
          <select
            className="sx-input"
            value={cat}
            aria-label="Filter by category"
            onChange={e => setCat(e.target.value)}
          >
            <option value="">All categories</option>
            {cats.map(c => (
              <option key={c.category} value={c.category}>
                {c.category} ({c.n})
              </option>
            ))}
          </select>
        </div>
        {err && (
          <p className="sx-sub" style={{ color: "#ff8a30" }}>
            {err}{" "}
            <button
              type="button"
              className="sx-btn"
              onClick={() => void load()}
            >
              Retry
            </button>
          </p>
        )}
        {skills === null && <p className="sx-sub">Loading the corpus…</p>}
        {skills !== null && skills.length === 0 && (
          <p className="sx-sub">No skills match this filter.</p>
        )}
        {skills !== null && (
          <div className="sk-list" role="list">
            {skills.map(s => (
              <div
                key={s.id}
                className={`sx-swrow${s.enabled ? "" : " off"}`}
                role="listitem"
              >
                <span className="sx-swtext">
                  <b className="sk-name">
                    {s.name}
                    {s.status !== "trusted" && (
                      <span className="sk-flag">{s.status}</span>
                    )}
                    {s.needs_review && <span className="sk-flag">review</span>}
                  </b>
                  <span className="sk-desc">{s.description || "—"}</span>
                  <span className="sk-meta mono">
                    {s.source} · {s.category} · used {s.use_count}×
                  </span>
                </span>
                <Switch
                  label={`Enable ${s.name}`}
                  checked={s.enabled}
                  onChange={(next: boolean) => void toggle(s, next)}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* commands (Phase C — the user's custom slash commands)                */
/* ------------------------------------------------------------------ */

function CommandsTab() {
  const settings = useChat(s => s.settings);
  const saveSettings = useChat(s => s.saveSettings);
  const cmds = settings?.custom_commands ?? [];
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [prompt, setPrompt] = useState("");
  const [saving, setSaving] = useState(false);
  // editing the row with this name (null = the add form is creating)
  const [editOf, setEditOf] = useState<string | null>(null);

  const persist = async (next: CustomCommand[], announcement: string) => {
    setSaving(true);
    try {
      await saveSettings({ custom_commands: next });
      toast(announcement);
      setEditOf(null);
      setName("");
      setDesc("");
      setPrompt("");
    } catch (e) {
      toast.error("Could not save the commands", {
        description:
          e instanceof BridgeError ? e.message : "bridge unreachable",
      });
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (c: CustomCommand) => {
    setEditOf(c.name);
    setName(c.name);
    setDesc(c.description);
    setPrompt(c.prompt);
  };

  const submit = () => {
    const cleanName = name.trim().replace(/^\/+/, "").toLowerCase();
    if (!/^[a-z0-9][a-z0-9-]*$/.test(cleanName)) {
      toast.error("Invalid command name", {
        description: "lowercase letters, digits and dashes — e.g. “review”",
      });
      return;
    }
    if (cmds.some(c => c.name === cleanName && c.name !== editOf)) {
      toast.error("A command with this name already exists");
      return;
    }
    const row: CustomCommand = {
      name: cleanName,
      description: desc.trim(),
      prompt: prompt.trim(),
    };
    if (!row.prompt) {
      toast.error("The prompt is required");
      return;
    }
    const next = editOf
      ? cmds.map(c => (c.name === editOf ? row : c))
      : [...cmds, row];
    void persist(next, editOf ? "Command updated" : "Command created");
  };

  return (
    <div>
      <p className="sx-sub">
        Your own slash commands for the composer — typing{" "}
        <b className="mono">/name</b> expands to the prompt. They run with your
        intent, exactly as if you had typed the prompt yourself.
      </p>
      <div className="sx-card">
        {cmds.length === 0 && editOf === null && (
          <p className="sx-sub">
            No custom commands yet — create the first one below.
          </p>
        )}
        {cmds.map(c => (
          <div key={c.name} className="sx-swrow">
            <span className="sx-swtext">
              <b className="mono">/{c.name}</b>
              <span className="sk-desc">{c.description || c.prompt}</span>
              <span className="sk-meta mono">{c.prompt}</span>
            </span>
            <span className="cm-acts">
              <button
                type="button"
                className="sx-btn"
                onClick={() => startEdit(c)}
                aria-label={`Edit /${c.name}`}
              >
                <Pencil size={12} strokeWidth={1.8} />
              </button>
              <button
                type="button"
                className="sx-btn"
                disabled={saving}
                aria-label={`Delete /${c.name}`}
                onClick={() =>
                  void persist(
                    cmds.filter(x => x.name !== c.name),
                    "Command deleted"
                  )
                }
              >
                <Trash2 size={12} strokeWidth={1.8} />
              </button>
            </span>
          </div>
        ))}
        <form
          className="sk-form"
          onSubmit={e => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="sk-form-title">
            {editOf ? `Editing /${editOf}` : "New command"}
          </div>
          <div className="sk-controls">
            <label className="sx-field">
              <span className="sx-flabel">Name</span>
              <input
                className="sx-input mono"
                value={name}
                maxLength={40}
                placeholder="review"
                onChange={e => setName(e.target.value)}
              />
            </label>
            <label className="sx-field">
              <span className="sx-flabel">Description</span>
              <input
                className="sx-input"
                value={desc}
                maxLength={120}
                placeholder="What it does (shown in the autocomplete)"
                onChange={e => setDesc(e.target.value)}
              />
            </label>
          </div>
          <label className="sx-field">
            <span className="sx-flabel">Prompt</span>
            <textarea
              className="sx-input sk-prompt"
              value={prompt}
              maxLength={4000}
              placeholder="The message that is sent when /name is typed"
              onChange={e => setPrompt(e.target.value)}
            />
          </label>
          <div className="am-form-actions">
            {editOf && (
              <button
                type="button"
                className="sx-btn"
                onClick={() => {
                  setEditOf(null);
                  setName("");
                  setDesc("");
                  setPrompt("");
                }}
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              className="sx-btn primary"
              disabled={saving || !name.trim() || !prompt.trim()}
            >
              {saving ? "Saving…" : editOf ? "Save changes" : "Add command"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function PlaceholderTab({ title, body }: { title: string; body: string }) {
  return (
    <div className="sx-card sx-ph">
      <h2>{title}</h2>
      <p className="sx-sub">{body}</p>
      <p className="sx-hint">
        This section mirrors the ZCode layout — its controls arrive with the
        phase above, never as dead switches.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* page                                                                */
/* ------------------------------------------------------------------ */

export default function SettingsPage() {
  const loadSettings = useChat(s => s.loadSettings);
  const settings = useChat(s => s.settings);
  const providerServing = useChat(s => s.providerServing);
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null);
  // report default: Model settings opens first; deep links (?section=)
  // resolve through the alias table so legacy + report names both land
  const [tab, setTab] = useState<TabId>(() => {
    const m = /[?&]section=([a-z-]+)/.exec(window.location.search);
    const wanted = m?.[1] ? SECTION_ALIAS[m[1]] : undefined;
    return wanted ?? "providers";
  });
  const [winMenu, setWinMenu] = useState(false);

  const winAction = (action: "minimize" | "maximize" | "close") => {
    setWinMenu(false);
    const g = (
      window as unknown as {
        universe?: {
          win?: (a: "minimize" | "maximize" | "close") => Promise<boolean>;
        };
      }
    ).universe;
    if (g?.win) void g.win(action);
    else toast("Window controls need the desktop app", { description: action });
  };

  useEffect(() => {
    void loadSettings();
  }, [loadSettings]);

  useEffect(() => {
    askConfirmImpl = (spec: ConfirmSpec) => setConfirm(spec);
    return () => {
      askConfirmImpl = () => {};
    };
  }, []);

  return (
    <div className="app-root">
      <div className="sx-page">
        <div className="sx-shell">
          {/* LEFT rail — sections live in the sidebar, Linear-style, never
              as a top tab row. Deep links (?section=) still land here. */}
          <nav className="sx-rail" aria-label="Settings sections">
            <Link
              href="/chat"
              className="sx-backrow"
              aria-label="Back to workspace"
            >
              <ArrowLeft size={14} strokeWidth={1.8} />
              Back to workspace
            </Link>
            <div className="sx-user-card" aria-label="Signed-in assistant">
              <span className="sx-user-glyph" aria-hidden="true">
                ✦
              </span>
              <span className="sx-user-name">
                {(settings?.assistant_name ?? "Universe").toUpperCase()}
              </span>
              <span className="sx-user-sub mono">
                {providerServing === "loading"
                  ? "…"
                  : providerServing && providerServing !== "none"
                    ? providerServing
                    : "no provider"}
              </span>
            </div>
            {GROUPS.map(g => (
              <div className="sx-rail-group" key={g.label}>
                <div className="sx-rail-label">{g.label}</div>
                {g.items.map(t => {
                  const Icon = t.icon;
                  return (
                    <button
                      key={t.id}
                      aria-current={tab === t.id ? "true" : undefined}
                      className={`sx-rail-btn${tab === t.id ? " on" : ""}`}
                      onClick={() => setTab(t.id)}
                    >
                      <Icon size={14} strokeWidth={1.8} />
                      {t.label}
                    </button>
                  );
                })}
              </div>
            ))}
            <Link href="/welcome?review=1" className="sx-onboard">
              Onboard
            </Link>
            <div className="sx-winmenu">
              <button
                type="button"
                className="sx-winmenu-btn"
                aria-haspopup="menu"
                aria-expanded={winMenu}
                aria-label="Window menu"
                onClick={() => setWinMenu(v => !v)}
              >
                Window menu
                <ChevronDown size={12} strokeWidth={1.9} />
              </button>
              {winMenu && (
                <div className="sx-winmenu-pop" role="menu" aria-label="Window">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => winAction("minimize")}
                  >
                    <Minus size={12} strokeWidth={1.9} /> Minimize
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => winAction("maximize")}
                  >
                    <Square size={12} strokeWidth={1.9} /> Maximize
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => winAction("close")}
                  >
                    <X size={12} strokeWidth={1.9} /> Close
                  </button>
                </div>
              )}
            </div>
          </nav>
          <div className="sx-content">
            <h1
              style={{
                fontSize: 22,
                fontWeight: 600,
                letterSpacing: "-0.4px",
                margin: "0 0 18px",
                color: "#d4d4d4",
              }}
            >
              {ALL_SECTIONS.find(s => s.id === tab)?.label ?? "Settings"}
            </h1>
            {tab === "general" && <GeneralTab />}
            {tab === "appearance" && (
              <div className="sx-card">
                <h2>Appearance</h2>
                <p className="sx-sub">
                  Theme is wired to System/Light/Dark in General → System. Live
                  font preview uses the sliders there — CSS vars
                  --ui-font-size/--code-font-size apply instantly without
                  restart.
                </p>
                <div className="sx-row">
                  <span>Preview</span>
                  <span
                    className="mono"
                    style={{ fontSize: (settings?.fontSize ?? 13) as number }}
                  >
                    Code 13px
                  </span>
                  <span
                    style={{ fontSize: (settings?.uiFontSize ?? 14) as number }}
                  >
                    UI 14px
                  </span>
                </div>
              </div>
            )}
            {tab === "providers" && <ProvidersTab />}
            {tab === "browser-use" && (
              <div className="sx-card">
                <h2>Browser Use</h2>
                <div className="sx-row">
                  <span>Headless</span>
                  <label className="sx-switch">
                    <input
                      type="checkbox"
                      checked={!!settings?.browserHeadless}
                      onChange={e =>
                        useChat
                          .getState()
                          .saveSettings({ browserHeadless: e.target.checked })
                      }
                    />
                    <span />
                  </label>
                </div>
                <div className="sx-row">
                  <span>Allow insecure certificates</span>
                  <label className="sx-switch">
                    <input
                      type="checkbox"
                      checked={!!settings?.browserAllowInsecureCerts}
                      onChange={e =>
                        useChat
                          .getState()
                          .saveSettings({
                            browserAllowInsecureCerts: e.target.checked,
                          })
                      }
                    />
                    <span />
                  </label>
                </div>
                <p className="sx-hint">
                  All navigations go through the consent gate — private URLs
                  escalate to per_turn.
                </p>
              </div>
            )}
            {tab === "computer-use" && (
              <div className="sx-card">
                <h2>Computer Use</h2>
                <div className="sx-row">
                  <span>Enable</span>
                  <label className="sx-switch">
                    <input
                      type="checkbox"
                      checked={!!settings?.computerUseEnabled}
                      onChange={e =>
                        useChat
                          .getState()
                          .saveSettings({
                            computerUseEnabled: e.target.checked,
                          })
                      }
                    />
                    <span />
                  </label>
                </div>
                <div className="sx-row">
                  <span>Composer entry</span>
                  <label className="sx-switch">
                    <input
                      type="checkbox"
                      checked={!!settings?.computerUseComposerEntry}
                      onChange={e =>
                        useChat
                          .getState()
                          .saveSettings({
                            computerUseComposerEntry: e.target.checked,
                          })
                      }
                    />
                    <span />
                  </label>
                </div>
                <p className="sx-hint">
                  Per-call Windows sandbox already enforced; these persist the
                  preference.
                </p>
              </div>
            )}
            {tab === "voice" && <VoiceTab />}
            {tab === "memory" && <MemoryTab />}
            {tab === "agents" && <SubagentsTab />}

            {tab === "mcp" && <McpTab />}
            {tab === "skills" && <SkillsTab />}
            {tab === "commands" && <CommandsTab />}
            {tab === "hooks" && <HooksTab />}
            {tab === "usage-stats" && (
              <div className="sx-card">
                <h2>Usage stats</h2>
                <UsageStatsTab />
              </div>
            )}
            {tab === "indexing" && (
              <div className="sx-card">
                <h2>Indexing</h2>
                <div className="sx-row">
                  <span>Enable</span>
                  <label className="sx-switch">
                    <input
                      type="checkbox"
                      checked={!!settings?.indexingEnabled}
                      onChange={e =>
                        useChat
                          .getState()
                          .saveSettings({ indexingEnabled: e.target.checked })
                      }
                    />
                    <span />
                  </label>
                </div>
                <input
                  className="sx-input"
                  placeholder="Root (default project root)"
                  value={settings?.indexingRoot ?? ""}
                  onChange={e =>
                    useChat
                      .getState()
                      .saveSettings({ indexingRoot: e.target.value })
                  }
                  style={{ marginTop: 8 }}
                />
                <button
                  className="sx-btn"
                  onClick={async () => {
                    const r = await api.resyncIndexing().catch(() => null);
                    if (r) toast.success("Resync queued");
                  }}
                >
                  Resync
                </button>
              </div>
            )}
          </div>
        </div>
        {confirm && (
          <ConfirmDialog spec={confirm} onClose={() => setConfirm(null)} />
        )}
      </div>
    </div>
  );
}
