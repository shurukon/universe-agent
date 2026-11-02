/**
 * components/desktop/Composer.tsx — the input (redesign 2026-09, ZCode
 * pass 2026-10). Auto-resize textarea, mic (voice channel), a tri-state
 * send button (idle · ready · stop), the model chip, and the ZCode chip
 * row: Switch mode (full/plan/ask) · Max (thinking level) · context
 * usage · attachments (v1: text file paths). While a goal runs the
 * composer follows Settings → interaction_behavior: "steer" (default)
 * course-corrects live; "queue" buffers the message as a follow-up and
 * shows the queued indicator.
 *
 * Slash commands (Phase C): typing "/" opens the inline autocomplete —
 * over the bridge's live registry — ZCode's 20 built-ins plus discovered
 * .md command files (zcode-guide contract). A built-in with an action runs
 * it; a custom command expands to its prompt on send; an unknown "/word"
 * is sent literally — the model sees what the user typed, never a silent
 * guess.
 */
import ContextMeter from "./ContextMeter";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  ClipboardEvent as ReactClipboardEvent,
  DragEvent as ReactDragEvent,
} from "react";
import { useLocation } from "wouter";
import {
  ArrowUp,
  Bug,
  Check,
  ChevronDown,
  Film,
  FileText,
  History,
  Image as ImageIcon,
  Mic,
  MicOff,
  MonitorDot,
  Moon,
  Plus,
  Presentation,
  ShieldCheck,
  Square,
  TriangleAlert,
  X,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";
import { useChat, type PermissionMode } from "@/stores/chat";
import { useRail } from "@/lib/flow/railStore";
import { useUi } from "@/stores/ui";
import { VoiceClient, type VoiceState } from "@/lib/voice";
import { useComposition } from "@/hooks/useComposition";
import ModelPicker from "@/components/desktop/ModelPicker";
import ProjectBar from "@/components/desktop/ProjectBar";
import { api } from "@/lib/api";

const VOICE_ERROR: Record<string, string> = {
  connecting: "Opening the voice channel…",
  "microphone unavailable": "No microphone available",
  "voice socket failed": "Voice channel unavailable",
};

/** the narrow typed view of the Electron preload surface the composer
 *  uses (same shape as preload.ts — pickFile/pasteClipboardMedia/
 *  pathForFile); undefined inside a plain browser */
type UniverseOS = {
  pickFile?: () => Promise<string | null>;
  pasteClipboardMedia?: () => Promise<string | null>;
  pathForFile?: (file: File) => string;
};
const universeOS = (): UniverseOS | undefined =>
  (window as unknown as { universe?: UniverseOS }).universe;

/** attachment chip icon by extension: images and video get their own
 *  glyph so the user can read the media kind at a glance */
const kindOf = (p: string): "image" | "video" | "text" => {
  const ext = (p.split(".").pop() ?? "").toLowerCase();
  if (
    ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif", "ico"].includes(
      ext
    )
  )
    return "image";
  if (
    [
      "mp4",
      "mov",
      "mkv",
      "webm",
      "avi",
      "wmv",
      "flv",
      "m4v",
      "mpg",
      "mpeg",
    ].includes(ext)
  )
    return "video";
  return "text";
};

type EffortLevel = "max" | "high" | "low";
/** ZCode-literal thinking levels (3.14.3 live UI): Max / High / Low. The
 *  bridge keeps its effort domain (low|medium|high) and a pick maps onto
 *  it one step down the order: Max→high, High→medium, Low→low. */
const EFFORTS: EffortLevel[] = ["max", "high", "low"];
const EFFORT_LABEL: Record<EffortLevel, string> = {
  max: "Max",
  high: "High",
  low: "Low",
};
const LV_TO_EFFORT: Record<EffortLevel, "low" | "medium" | "high"> = {
  max: "high",
  high: "medium",
  low: "low",
};

const MODES: Array<{
  id: PermissionMode;
  label: string;
  note: string;
}> = [
  {
    id: "plan",
    label: "Plan mode",
    note: "Explore and read only — no tools until plan approved",
  },
  {
    id: "edit",
    label: "Edit automatically",
    note: "Writes auto; some commands ask for approval",
  },
  {
    id: "ask",
    label: "Ask before changes",
    note: "Asks before any edit — approval required",
  },
  {
    id: "full",
    label: "Full access",
    note: "No approvals — runs directly without stopping",
  },
];

/** ZCode's home shows a row of template chips under the card. Universe keeps
 *  the surface honest: a chip writes a REAL opening prompt into the
 *  composer (or opens the page it names) — no fake template engine. */
const SUGGESTS: Array<{
  id: string;
  label: string;
  icon: LucideIcon;
  prompt: string;
  to?: string;
}> = [
  {
    id: "weekly-summary",
    label: "Weekly Summary",
    icon: History,
    prompt:
      "Summarize this week in this workspace — what shipped, what is still open, and what needs my attention next.",
  },
  {
    id: "error-fix",
    label: "Error Fix",
    icon: Bug,
    prompt:
      "Find the current errors in this project, explain the root cause, then fix them and re-run the checks.",
  },
  {
    id: "ppt-creation",
    label: "PPT Creation",
    icon: Presentation,
    prompt:
      "Build a short slide deck: outline the story first, then draft the slides one by one.",
  },
  {
    id: "off-peak",
    label: "Idle-time task",
    icon: Moon,
    prompt: "",
    to: "/automations",
  },
];

export default function Composer({ centered = false }: { centered?: boolean }) {
  const [text, setText] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [, navigate] = useLocation();
  const sendGoal = useChat(s => s.sendGoal);
  const cancelGoal = useChat(s => s.cancelGoal);
  const steerMessage = useChat(s => s.steerMessage);
  // stable selector: a fresh undefined per snapshot would loop React's
  // update-depth guard the same way a fresh array does
  //
  // Scoped to the task on screen: a task running in the background must not
  // leak a "Working / Stop" strip into an unrelated conversation (or an
  // empty new task), nor block sending there.
  const runningGoal = useChat(s => {
    const viewId = s.viewedGoalId ?? s.activeTaskId;
    if (!viewId) return null;
    return (
      s.tasks.find(t => t.turn_id === viewId && t.status === "running") ?? null
    );
  });
  const connection = useChat(s => s.connection);
  const settings = useChat(s => s.settings);
  const permissionMode = useChat(s => s.permissionMode);
  const switchPermissionMode = useChat(s => s.switchPermissionMode);
  // synchronous in-flight mirror of the store lock: the button and the
  // Enter path share it, so a second submit inside the POST window is
  // dead on arrival (the old flow re-armed between renders and duplicated)
  const sending = useChat(s => s.sending);
  const [voiceState, setVoiceState] = useState<VoiceState>("off");
  const voiceRef = useRef<VoiceClient | null>(null);

  // the ZCode chips: mode + thinking level + computer use — the level list
  // also lives in the [+] menu; the bar carries the permission + computer
  // chips exactly like ZCode 3.14.3's follow-up bar
  const [level, setLevel] = useState<EffortLevel>("max");
  const [permMenu, setPermMenu] = useState(false);
  const computerUse = useUi(s => s.computerUse);
  const toggleComputerUse = useUi(s => s.toggleComputerUse);
  const [attachOpen, setAttachOpen] = useState(false);
  const [attachments, setAttachments] = useState<string[]>([]);

  // ---- slash commands (ZCode's live registry) -----------------------------
  // The bridge owns the menu: ZCode's 20 built-ins + discovered .md command
  // files. Built-ins with an action run it; the honest placeholders say so
  // instead of pretending; custom commands insert "/name " so args follow.
  const [slashCommands, setSlashCommands] = useState<
    Array<{
      name: string;
      summary: string;
      usage: string;
      action: string;
      supported: boolean;
      kind?: string;
    }>
  >([]);
  useEffect(() => {
    let alive = true;
    api
      .listCommands()
      .then(r => {
        if (alive) setSlashCommands(r.commands);
      })
      .catch(() => {
        /* bridge down: the popup stays closed, not a fake list */
      });
    return () => {
      alive = false;
    };
  }, []);

  const runAction = (action: string, args?: string) => {
    if (action === "new") {
      useChat.getState().newConversation();
      navigate("/chat");
    } else if (action === "settings") navigate("/settings");
    else if (action === "settings:skills") navigate("/settings?section=skills");
    else if (action === "settings:hooks") navigate("/settings?section=hooks");
    else if (action === "settings:mcp") navigate("/settings?section=mcp");
    else if (action === "automations") navigate("/automations");
    else if (action === "terminal") {
      const rail = useRail.getState();
      if (rail.open && rail.tab === "terminal") rail.closeRail();
      else rail.openRail("terminal");
    } else if (action === "mode") {
      // /mode with no arg: guide to the mode chip (Plan/Build/Edit/Auto/Yolo)
      taRef.current?.focus();
      toast("Switch mode from the chip row — Plan/Build/Edit/Auto/Yolo");
    } else if (action === "permission") {
      const m = (args || "").trim() as PermissionMode;
      if (m === "plan" || m === "ask" || m === "edit" || m === "full") {
        void switchPermissionMode(m);
        toast(`Permission mode: ${m}`);
      } else {
        taRef.current?.focus();
        toast("Switch mode from the chip row — Plan/Build/Edit/Auto/Yolo");
      }
    } else if (action === "planmode") {
      void switchPermissionMode("plan");
      toast("Plan mode: research read-only until the plan is approved");
    } else if (action === "skills" || action === "skill") {
      if (action === "skill") {
        // seed the composer with the arg form; a bare /skill lists them
        if (args?.trim()) {
          setText(`/skill ${args.trim()}`);
          taRef.current?.focus();
        } else {
          navigate("/settings?section=skills");
        }
      } else {
        navigate("/settings?section=skills");
      }
    } else if (action === "hooks") navigate("/settings?section=hooks");
    else if (
      action === "memory" ||
      action === "status" ||
      action === "version" ||
      action === "doctor" ||
      action === "context"
    ) {
      void (async () => {
        try {
          if (action === "memory") {
            const t = await api.statusParity();
            const m = t.memory;
            toast(
              m
                ? `Memory: ${m.skills} skills, ${m.orbits} orbits, ${m.systems} systems`
                : `Memory unavailable (${t.memory_error ?? "no data"})`
            );
          } else {
            const t = await api.statusParity();
            const lines: Record<string, string> = {
              status: `Mode ${t.permission_mode ?? "?"} · ${t.active_goals} active task(s) · ready=${t.ready ? "yes" : "no"}`,
              version: t.version,
              doctor: t.init_error
                ? `Health check: ${t.init_error}`
                : `Health check OK · uptime ${Math.round(t.uptime_s)}s · mode ${t.permission_mode ?? "?"}`,
              context: `Context meter fills while a task runs · ${t.active_goals} active task(s)`,
            };
            toast(lines[action] ?? t.version);
          }
        } catch {
          toast("Could not read the status from the bridge");
        }
      })();
    } else if (action === "agents") {
      void (async () => {
        try {
          const t = await api.agentRoster();
          toast(t.agents.map(a => a.name).join(", "));
        } catch {
          toast("Could not read the agent roster");
        }
      })();
    } else if (action === "export") {
      void (async () => {
        try {
          const t = await api.exportTranscript(runningGoal?.turn_id ?? "");
          toast(t.path ? `Transcript saved: ${t.path}` : "Nothing to export");
        } catch {
          toast("Export failed — no task transcript?");
        }
      })();
    } else if (action === "model" || action === "effort") {
      taRef.current?.focus();
    }
  };

  const SLASH_TOKEN_RE = /^\/([a-zA-Z0-9:_-]*)$/;
  const slashToken = SLASH_TOKEN_RE.exec(text);
  const [slashIdx, setSlashIdx] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  useEffect(() => {
    setSlashDismissed(false);
    setSlashIdx(0);
  }, [text]);
  const slashQuery = slashToken ? slashToken[1] : null;
  const slashMatches = useMemo(() => {
    if (slashQuery === null) return [];
    return slashQuery
      ? slashCommands.filter(c => c.name.startsWith(slashQuery))
      : slashCommands;
  }, [slashQuery, slashCommands]);
  const slashVisible =
    !!slashToken && !slashDismissed && slashMatches.length > 0;

  const runSlash = (cmd: (typeof slashCommands)[number]) => {
    if (cmd.action && cmd.kind !== "custom") {
      setText("");
      const argPart = cmd.usage.split(" ").slice(1).join(" ");
      runAction(cmd.action, argPart);
      return;
    }
    if (cmd.kind === "custom") {
      // keep the slash form in the input — args can follow, the prompt
      // expands on send (the bridge resolves it) so the user always sees
      // what will be sent
      setText(`/${cmd.name} `);
      taRef.current?.focus();
      return;
    }
    // ZCode has this command, Universe has no counterpart yet — say so plainly
    setText("");
    toast(
      `${cmd.usage.split(" ")[0]} is a ZCode command — Universe lands it in a later phase`,
      {
        description: cmd.summary,
      }
    );
  };

  const queueMode = settings?.interaction_behavior === "queue";
  // Phase 3: the General → Chat view "Attachments" switch gates "+ Add
  // context" (default on — only an explicit off hides the control)
  const attachEnabled = settings?.attachments_enabled !== false;

  // Phase 3: General → Default thinking level seeds the Max chip for NEW
  // messages; a per-message pick wins until the setting changes again
  useEffect(() => {
    const d = settings?.default_thinking_level;
    // stored on the effort domain (low|medium|high) — remap into the ZCode
    // vocabulary by strength: low→Low, medium→High, high→Max
    if (d === "low") setLevel("low");
    else if (d === "medium") setLevel("high");
    else if (d === "high") setLevel("max");
  }, [settings?.default_thinking_level]);

  const busy = !!runningGoal;
  // while a goal runs the composer STAYS a live control (M3): in steer mode
  // Enter course-corrects the running goal; in queue mode it buffers a
  // follow-up the bridge runs when the goal ends. Stop lives on its button.
  const canSend =
    text.trim().length > 0 && !sending && connection !== "offline";
  const canStop = busy && !sending;
  // a goal stuck "running" (missed terminal WS event, bridge restart)
  // freezes send with no explanation — say why, with the way out
  const busyTitle = runningGoal
    ? `A goal is still running ("${runningGoal.text.slice(0, 60)}") — Stop it or press Esc to send a new one`
    : undefined;
  const composition = useComposition<HTMLTextAreaElement>();

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
  }, [text]);

  // Edit on a sent user message: prefill the composer with that text and
  // focus it. The threaded model continues the session, so ZCode's
  // branch-and-resend maps to seed + focus + send through the live path.
  const seed = useUi(s => s.composerSeed);
  useEffect(() => {
    if (!seed) return;
    setText(seed.text);
    const ta = taRef.current;
    if (ta) {
      ta.focus();
      const at = ta.value.length;
      ta.setSelectionRange(at, at);
    }
    useUi.getState().clearSeed();
  }, [seed]);

  useEffect(() => {
    return () => voiceRef.current?.stop();
  }, []);

  const submit = async () => {
    let body = text.trim();
    if (!body && canStop) {
      if (runningGoal) await cancelGoal(runningGoal.turn_id);
      return;
    }
    if (!canSend) return;
    // ZCode slash resolution: the bridge owns the registry. A .md command
    // expands to its prompt ($ARGUMENTS rides along); a bare built-in runs
    // its action; anything else is sent literally — never a silent guess.
    // (/goal is DELETED 2026-10-28: one engine runs every task; unknown
    // slashes resolve through the bridge like any other command.)
    if (body.startsWith("/")) {
      try {
        const r = await api.resolveCommand(body);
        const res = r.command;
        if (res) {
          if (res.kind === "custom" && res.prompt) {
            body = res.args ? `${res.prompt}\n\n${res.args}` : res.prompt;
          } else if (res.kind === "builtin") {
            const cmd = res.command as { action?: string } | undefined;
            if (cmd?.action) {
              setText("");
              runAction(cmd.action);
              return;
            }
            // unsupported ZCode command: honest placeholder, nothing sent
            setText("");
            toast(
              `${res.name} is a ZCode command — Universe lands it in a later phase`,
              {
                description: (res.command as { summary?: string })?.summary,
              }
            );
            return;
          }
        }
      } catch {
        /* bridge down: send literally — never a silent guess */
      }
    }
    if (runningGoal && !queueMode) {
      // steer: course-correct the running goal, never a second goal
      const ok = await steerMessage(body);
      if (ok) {
        setText("");
        taRef.current?.focus();
      }
      return;
    }
    // queue mode: a running goal gets the follow-up buffered server-side
    // (sendGoal reports "queued" and keeps no bubble — turn_started mints
    // it when the drain fires); idle sends run as always
    const ok = await sendGoal(body, {
      attachments: attachments.length ? attachments : undefined,
      effort: LV_TO_EFFORT[level],
    });
    if (ok !== null) {
      setText("");
      setAttachments([]);
      taRef.current?.focus();
    }
  };

  // professional touch: Esc interrupts the running goal from the composer —
  // but never while a consent modal owns the keyboard (its Esc denies),
  // never while the command palette is open (its Esc closes the palette —
  // Ctrl+K then Esc must not kill the goal behind the overlay), and never
  // while an ask_user question waits (its Enter answers)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        e.key === "Escape" &&
        runningGoal &&
        useChat.getState().consents.length === 0 &&
        useChat.getState().questions.length === 0 &&
        !useUi.getState().paletteOpen
      ) {
        e.preventDefault();
        void cancelGoal(runningGoal.turn_id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [runningGoal, cancelGoal]);

  const toggleVoice = async () => {
    if (voiceRef.current && voiceRef.current.state !== "off") {
      voiceRef.current.stop();
      return;
    }
    // voice_enabled gates the server channel (bridge closes it otherwise) —
    // check our own settings first instead of opening a doomed socket
    const st0 = useChat.getState();
    if (!st0.settings) {
      try {
        await st0.loadSettings();
      } catch {
        /* offline: fall through to the socket error path */
      }
    }
    if (
      useChat.getState().settings &&
      !useChat.getState().settings!.voice_enabled
    ) {
      toast.error("Voice mode is off — enable it in Settings → Voice first", {
        description: "Voice input",
      });
      return;
    }
    const client = new VoiceClient({
      onState: (s, detail) => {
        setVoiceState(s);
        if (detail) {
          const human = Object.entries(VOICE_ERROR).find(([k]) =>
            detail.startsWith(k)
          );
          toast.error(human ? human[1] : detail, {
            description: "Voice input",
          });
        }
        // (the universe core's mic-level breathing is driven by the
        // per-frame 'universe:voice' events VoiceClient dispatches itself)
      },
      onTranscript: t => {
        // a spoken goal enters the same pipeline as a typed one — with the
        // same busy/offline gate, or voice would double-submit / send dead
        const st = useChat.getState();
        if (
          st.tasks.some(x => x.status === "running") ||
          st.connection === "offline"
        ) {
          toast.error(
            st.connection === "offline"
              ? "Bridge is offline — voice goal dropped"
              : "A goal is already running — voice goal dropped",
            { description: "Voice input" }
          );
          return;
        }
        void sendGoal(t);
      },
      onTts: mp3 => {
        // TTS mp3 frames arrived with no player and were silently dropped —
        // play them through a throwaway audio element
        try {
          const blob = new Blob([mp3], { type: "audio/mpeg" });
          const url = URL.createObjectURL(blob);
          const el = new Audio(url);
          el.onended = () => URL.revokeObjectURL(url);
          el.onerror = () => URL.revokeObjectURL(url);
          void el.play().catch(() => URL.revokeObjectURL(url));
        } catch {
          /* autoplay blocked or codec missing: transcript already shown */
        }
      },
    });
    voiceRef.current = client;
    await client.start();
    if (client.state === "listening") {
      window.dispatchEvent(new Event("universe:dispatch"));
    }
  };

  const micLive = voiceState !== "off";

  // ZCode's live composer (2026-10-24 reference): an empty/new-task
  // surface says "Ask ZCode anything, @ to add context, / for commands or
  // capabilities"; an OPEN session says "Ask for follow-up changes"
  // whether the goal runs or has finished (measured 2026-10-23)
  const placeholder = centered
    ? "Ask Universe anything, @ to add context, / for commands or capabilities"
    : queueMode
      ? "Keep typing to queue follow-up changes"
      : "Ask for follow-up changes";

  // "/" opens the inline slash autocomplete (Phase C) — the popup keys are
  // handled in the textarea's onKeyDown below; typing a path or fraction
  // (any "/" with a space before it) never triggers it because the whole
  // input must be one "/token".

  /** add a file path picked from the OS file explorer */
  const addPath = (path: string) => {
    const p = path.trim().replace(/^["']|["']$/g, "");
    if (!p) return;
    if (attachments.some(a => a.toLowerCase() === p.toLowerCase())) return;
    setAttachments(a => [...a, p]);
  };

  /** paste in the composer: a copied image/video FILE (Explorer copy →
   *  Ctrl+V) resolves to its real path via webUtils; a pasted screenshot
   *  bitmap (no file origin — Electron 43 removed File.path) is saved by
   *  the main process and attached as a PNG. Plain-text paste untouched. */
  const onComposerPaste = async (
    e: ReactClipboardEvent<HTMLTextAreaElement>
  ) => {
    if (!attachEnabled) return; // setting off: default paste, never eat it
    const g = universeOS();
    const dt = e.clipboardData;
    const paths: string[] = [];
    for (const f of Array.from(dt?.files ?? [])) {
      try {
        const p = g?.pathForFile?.(f) || "";
        if (p) paths.push(p);
      } catch {
        /* webUtils unavailable for this item — the bitmap fallback below handles screenshots */
      }
    }
    const hasFileItem = Array.from(dt?.items ?? []).some(
      i => i.kind === "file"
    );
    if (!paths.length && !hasFileItem) return; // plain-text paste — default
    e.preventDefault();
    for (const p of paths) addPath(p);
    if (paths.length) return;
    // bitmap-only paste (a screenshot): the main process writes one PNG
    // into the OS temp dir and returns its real path
    try {
      const bitmap = await g?.pasteClipboardMedia?.();
      if (bitmap) {
        addPath(bitmap);
        taRef.current?.focus();
      } else {
        toast.error(
          "Clipboard holds no readable image — copy the file itself, or attach it with +"
        );
      }
    } catch {
      toast.error("Pasting media needs the desktop app");
    }
  };

  /** drop on the composer card: any real file (image, video, doc)
   *  attaches by its absolute path — webUtils is the Electron 43 way */
  const onComposerDrop = (e: ReactDragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const g = universeOS();
    let added = 0;
    for (const f of Array.from(e.dataTransfer?.files ?? [])) {
      try {
        const p = g?.pathForFile?.(f) || "";
        if (p) {
          addPath(p);
          added += 1;
        }
      } catch {
        /* path unresolvable — the item is skipped, never a fake chip */
      }
    }
    if (added) {
      setAttachOpen(false);
      taRef.current?.focus();
    }
  };

  const onComposerDragOver = (e: ReactDragEvent<HTMLDivElement>) => {
    e.preventDefault(); // allow the drop (media + docs attach by absolute path)
  };

  /** a suggested-prompt chip: write a real opening prompt (never clobber an
   *  existing draft — append it) or open the surface the chip names */
  const runSuggest = (s: (typeof SUGGESTS)[number]) => {
    if (s.to) {
      navigate(s.to);
      return;
    }
    setText(prev => (prev.trim() ? `${prev}\n\n${s.prompt}` : s.prompt));
    // put the caret after the inserted prompt (setTimeout, not rAF: the
    // renderer throttles animation frames while the window is occluded)
    setTimeout(() => {
      const ta = taRef.current;
      if (!ta) return;
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }, 0);
  };

  return (
    <div className={`composer-wrap${centered ? " centered" : ""}`}>
      {/* ZCode's running composer is bare: the queue semantics live in the
         placeholder ("Keep typing to queue follow-up changes") — no extra
         status rows above the input. On the new-task surface the card sits
         inside the workspace shell that carries the crumb band (measured
         anatomy: 40px band → 16px card, 12px padding, 12px row gap). */}
      <div className={`zc-shell${centered ? " has-head" : ""}`}>
        {centered && <ProjectBar inCard />}
        <div
          className="composer"
          onDragOver={onComposerDragOver}
          onDrop={onComposerDrop}
        >
          {attachments.length > 0 && (
            <div className="cx-attach-row">
              {attachments.map(a => (
                <span key={a} className="cx-attach-chip">
                  {kindOf(a) === "image" ? (
                    <ImageIcon size={10} strokeWidth={2} aria-hidden="true" />
                  ) : kindOf(a) === "video" ? (
                    <Film size={10} strokeWidth={2} aria-hidden="true" />
                  ) : (
                    <FileText size={10} strokeWidth={2} aria-hidden="true" />
                  )}
                  <span title={a}>{a.split(/[\\/]/).pop() ?? a}</span>
                  <button
                    type="button"
                    onClick={() =>
                      setAttachments(list => list.filter(x => x !== a))
                    }
                    aria-label={`Remove ${a}`}
                  >
                    <X size={10} strokeWidth={2.2} />
                  </button>
                </span>
              ))}
            </div>
          )}
          {attachEnabled && attachOpen && (
            <div className="add-menu" role="menu" aria-label="Add context">
              <button
                type="button"
                className="add-item"
                role="menuitem"
                onClick={() => {
                  setAttachOpen(false);
                  const g = (
                    window as unknown as {
                      universe?: {
                        pickFile?: () => Promise<string | null>;
                      };
                    }
                  ).universe;
                  if (g?.pickFile) {
                    void g.pickFile().then(p => {
                      if (p) addPath(p);
                    });
                  } else {
                    toast.error("File picker needs the desktop app");
                  }
                }}
              >
                <FileText size={12} strokeWidth={1.8} />
                Add file
              </button>
              <div className="add-hints">
                <span>
                  use <b className="mono">/</b> for capabilities
                </span>
                <span>
                  use <b className="mono">$</b> for skills
                </span>
                <span>
                  use <b className="mono">#</b> to add context (chat)
                </span>
                <span>paste or drop images &amp; video — attached by path</span>
              </div>
              {/* Permission mode lives here now (ZCode-literal bar): the chip
                was a Universe-only bar control; the switching stays real and
                reachable from the same [+] menu */}
              <div className="add-sep" role="separator" aria-hidden="true" />
              {MODES.map(m => (
                <button
                  key={m.id}
                  type="button"
                  className={`add-item${permissionMode === m.id ? " on" : ""}`}
                  role="menuitemradio"
                  aria-checked={permissionMode === m.id}
                  onClick={() => {
                    setAttachOpen(false);
                    if (permissionMode !== m.id)
                      void switchPermissionMode(m.id);
                  }}
                >
                  <ShieldCheck size={12} strokeWidth={1.8} />
                  <span>{m.label}</span>
                  <span className="cx-lv">{m.note}</span>
                </button>
              ))}
              <div className="add-sep" role="separator" aria-hidden="true" />
              {EFFORTS.map(e => (
                <button
                  key={e}
                  type="button"
                  className={`add-item${level === e ? " on" : ""}`}
                  role="menuitemradio"
                  aria-checked={level === e}
                  title="Thinking level — mapped one step down onto the bridge's effort domain"
                  onClick={() => setLevel(e)}
                >
                  {level === e && <Check size={12} strokeWidth={2} />}
                  <span>{EFFORT_LABEL[e]}</span>
                  <span className="cx-lv">{LV_TO_EFFORT[e]} effort</span>
                </button>
              ))}
              <div className="add-sep" role="separator" aria-hidden="true" />
              <button
                type="button"
                className="add-item"
                role="menuitem"
                onClick={() => {
                  setAttachOpen(false);
                  void toggleVoice();
                }}
              >
                {micLive ? (
                  <MicOff size={12} strokeWidth={1.8} />
                ) : (
                  <Mic size={12} strokeWidth={1.8} />
                )}
                {micLive ? "Stop voice input" : "Voice input"}
              </button>
            </div>
          )}
          {slashVisible && (
            <div
              className="slash-pop"
              role="listbox"
              aria-label="Slash commands"
            >
              {slashMatches.map((c, i) => (
                <button
                  key={c.name}
                  type="button"
                  role="option"
                  aria-selected={i === slashIdx}
                  className={`slash-item${i === slashIdx ? " on" : ""}`}
                  onMouseEnter={() => setSlashIdx(i)}
                  onMouseDown={e => {
                    // mousedown, not click: the textarea must not blur first
                    e.preventDefault();
                    runSlash(c);
                  }}
                >
                  <b className="mono">/{c.name}</b>
                  <span className="slash-desc">{c.summary}</span>
                </button>
              ))}
            </div>
          )}
          <textarea
            ref={taRef}
            rows={1}
            placeholder={placeholder}
            value={text}
            onChange={e => setText(e.target.value)}
            onCompositionStart={composition.onCompositionStart}
            onCompositionEnd={composition.onCompositionEnd}
            onKeyDown={e => {
              // IME composing (CJK etc.): Enter confirms the candidate window,
              // it must never submit the half-composed text — and every "/"
              // key below only reaches the autocomplete post-composition
              if (composition.isComposing()) return;
              // slash autocomplete (Phase C): navigate · select · dismiss
              if (slashVisible) {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setSlashIdx(i => Math.min(i + 1, slashMatches.length - 1));
                  return;
                }
                if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setSlashIdx(i => Math.max(i - 1, 0));
                  return;
                }
                if (
                  (e.key === "Enter" || e.key === "Tab") &&
                  slashMatches[slashIdx]
                ) {
                  e.preventDefault();
                  runSlash(slashMatches[slashIdx]);
                  return;
                }
                if (e.key === "Escape") {
                  e.preventDefault();
                  setSlashDismissed(true);
                  return;
                }
              }
              // a held-down Enter auto-repeats keydown — every repeat that
              // passed here fired a fresh POST during the network window.
              // ACTION keys only: repeats of TEXT keys (letters, space,
              // Backspace) must reach the textarea — swallowing them broke
              // held-Backspace deletion (user bug 2026-10-30)
              if (e.repeat) {
                if (
                  e.key === "Enter" ||
                  e.key === "Tab" ||
                  e.key === "Escape"
                ) {
                  e.preventDefault();
                }
                return;
              }
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            aria-label="Goal input"
            onPaste={e => {
              void onComposerPaste(e);
            }}
            disabled={connection === "offline"}
          />
          <div className="composer-bar">
            {/* "+" leads the bar — ZCode's add-context slot */}
            <button
              className="icon-btn"
              disabled={!attachEnabled}
              onClick={() => {
                setAttachOpen(v => !v);
                setPermMenu(false);
              }}
              title={
                attachEnabled
                  ? "Add context — attach files, or paste / drop images and video"
                  : "Attachments are off — enable them in Settings → General"
              }
              aria-label="Add context files"
              aria-expanded={attachOpen}
            >
              <Plus size={16} strokeWidth={2} />
            </button>

            {/* ZCode-literal bar: the permission chip (orange when the mode
              lets the agent act without the gate) + the Computer Use chip;
              the thinking-level list moved into the [+] menu */}
            <span className="cx-pop">
              <button
                type="button"
                className={`cx-chip perm${
                  permissionMode === "edit" || permissionMode === "full"
                    ? ""
                    : " calm"
                }`}
                onClick={() => setPermMenu(v => !v)}
                title={`Permission: "${MODES.find(m => m.id === permissionMode)?.label}" — ${MODES.find(m => m.id === permissionMode)?.note}`}
                aria-label="Permission mode"
                aria-expanded={permMenu}
              >
                {permissionMode === "edit" || permissionMode === "full" ? (
                  <TriangleAlert size={16} strokeWidth={2} />
                ) : (
                  <ShieldCheck size={16} strokeWidth={2} />
                )}
                <b>{MODES.find(m => m.id === permissionMode)?.label}</b>
                <ChevronDown size={14} strokeWidth={2} />
              </button>
              {permMenu && (
                <span
                  className="cx-menu"
                  role="menu"
                  aria-label="Permission modes"
                >
                  {MODES.map(m => (
                    <button
                      key={m.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={permissionMode === m.id}
                      className={permissionMode === m.id ? "on" : ""}
                      onClick={() => {
                        if (permissionMode !== m.id)
                          void switchPermissionMode(m.id);
                        setPermMenu(false);
                      }}
                    >
                      <span>{m.label}</span>
                      <span className="cx-note">{m.note}</span>
                    </button>
                  ))}
                </span>
              )}
            </span>

            <span className="cx-pop">
              <button
                type="button"
                className={`cx-chip comp${computerUse ? " on" : ""}`}
                onClick={toggleComputerUse}
                aria-pressed={computerUse}
                title="Computer Use — let the agent drive the screen (flag saved with the next task)"
              >
                <MonitorDot size={16} strokeWidth={2} />
                <b>Computer Use</b>
                {/* ZCode's green activity dot — lit only while the flag is on */}
                <span className="comp-dot" aria-hidden="true" />
              </button>
            </span>

            <span className="flex-sp" />

            <ContextMeter />

            {/* the model chip sits on the right, like ZCode's: provider +
              model live in the chat surface, Settings manages keys only */}
            <ModelPicker />

            {/* the model chip carries the provider+model name (ZCode right
              slot); voice input lives in the [+] menu — the bar matches
              ZCode exactly */}
            {canStop && (
              <button
                className="send-btn stop"
                onClick={() => {
                  if (runningGoal) void cancelGoal(runningGoal.turn_id);
                }}
                title={busyTitle ?? "Stop"}
                aria-label="Stop the running goal"
              >
                <Square size={10} strokeWidth={2.5} fill="currentColor" />
              </button>
            )}
            <button
              className={`send-btn${canSend ? " ready" : ""}`}
              onClick={() => void submit()}
              disabled={!canSend}
              title={
                runningGoal
                  ? queueMode
                    ? "Queue this follow-up — runs when the goal ends"
                    : "Steer the running goal — applied at its next step"
                  : "Send"
              }
              aria-label={runningGoal ? "Queue follow-up" : "Send goal"}
            >
              <ArrowUp size={16} strokeWidth={2} />
            </button>
          </div>
        </div>
      </div>
      {centered && (
        <div
          className="zc-suggests"
          role="group"
          aria-label="Suggested prompts"
        >
          {SUGGESTS.map(s => {
            const Icon = s.icon;
            return (
              <button
                key={s.id}
                type="button"
                className="zc-suggest"
                onClick={() => runSuggest(s)}
              >
                <Icon strokeWidth={2} aria-hidden="true" />
                {s.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
