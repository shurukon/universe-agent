/**
 * pages/Bots.tsx — the Bots workspace: a Grok-Bot-style IM surface pinned
 * under the topbar (h-[calc(100vh-56px)]), styled on the SAME neutral
 * design system as the Universe shell (design-system.css tokens — the
 * blue-tinted palette was retired 2026-11).
 *
 *   SIDEBAR  w-[264px] — the shell's left column in Bots mode: the
 *            workspace switcher on top, then the IM-style bot list
 *            (avatar, name, last message, status dot) + New Bot.
 *   CENTER   flex-1   — floating bot pill header (avatar, name, status),
 *                       approvals banner, transcript bubbles (user right
 *                       surface, bot left panel), task composer card.
 *   RIGHT PANEL 40%  — collapsible tabs: Screen (live iframe of the bot's
 *                       machine, token via query param), Details (identity,
 *                       TASK→SKILL→ROUTINE stepper, save-as-skill, schedule
 *                       editor, danger zone), Library (skills/Orbits).
 *
 * Data: lib/botsApi.ts against /api/bots* — the selected bot is polled
 * every 4s; the rail sweep refreshes quietly every 15s. User sends are
 * appended optimistically and reconciled (deduped by text) on the next
 * transcript fetch.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Clock,
  Loader2,
  Maximize2,
  PanelRight,
  PlayCircle,
  Plus,
  Save,
  SendHorizonal,
  ShieldAlert,
  Sparkles,
  Square,
  Trash2,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { BridgeError } from "@/lib/api";
import {
  botScreenUrl,
  clearBotSchedule,
  createBot,
  decideApproval,
  deleteBot,
  getBot,
  listApprovals,
  listBots,
  runBot,
  setBotSchedule,
  setBotSkill,
  stopBot,
} from "@/lib/botsApi";
import type {
  ApprovalDecision,
  Bot,
  BotApproval,
  BotDetail,
  BotEvent,
  BotSchedule,
  ScheduleKind,
} from "@/lib/botsApi";
import { cn } from "@/lib/utils";
import WorkspaceSwitcher from "@/components/desktop/WorkspaceSwitcher";

/* ---- design tokens — design-system.css (ZCode-measured neutrals) -------- */

/* ZCode primary: the light square button (send-btn.ready language) */
const ACCENT_BTN = "bg-[#d4d4d4] text-[#161616] hover:bg-white";
const FIELD =
  "h-8 w-full border-[var(--ds-line)] bg-white/[0.03] text-xs";
const SECTION_TITLE =
  "text-[11px] font-semibold uppercase tracking-wider text-muted-foreground";

const STATUS_META: Record<
  Bot["status"],
  { label: string; dot: string; badge: string; pulse?: boolean }
> = {
  idle: {
    label: "Idle",
    dot: "#5e5e5e",
    badge: "border-[var(--ds-line)] bg-white/[0.04] text-[var(--ds-ink-3)]",
  },
  running: {
    label: "Running",
    dot: "#46bf72",
    badge:
      "border-[rgba(70,191,114,0.3)] bg-[rgba(70,191,114,0.12)] text-[var(--ds-accent)]",
    pulse: true,
  },
  waiting_approval: {
    label: "Waiting approval",
    dot: "#ff8a30",
    badge:
      "border-[rgba(255,138,48,0.3)] bg-[rgba(255,138,48,0.1)] text-[#ffb37a]",
  },
  error: {
    label: "Error",
    dot: "#e5484d",
    badge:
      "border-[rgba(229,72,77,0.3)] bg-[rgba(229,72,77,0.1)] text-[#ff8589]",
  },
  paused: {
    label: "Paused",
    dot: "#414141",
    badge: "border-[var(--ds-line)] bg-white/[0.03] text-[var(--ds-ink-4)]",
  },
};

/* ---- small helpers -------------------------------------------------------- */

function errText(e: unknown): string {
  if (e instanceof BridgeError) return e.message;
  if (e instanceof Error && e.message) return e.message;
  return "Universe bridge is not responding";
}

function fmtClock(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fmtStamp(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}

/** Chip text for a bot schedule — honest, one line. */
function describeSchedule(s: BotSchedule): string {
  switch (s.kind) {
    case "daily": {
      const tz = s.tz ? ` ${s.tz}` : "";
      return s.time ? `Every day ${s.time}${tz}` : "Every day";
    }
    case "interval":
      return s.expr ? `interval ${s.expr}` : "interval";
    case "cron":
      return s.expr || "cron";
    default:
      return "No schedule";
  }
}

/** "30m" | "30" → "30"; anything else → "" (forces a retype). */
function parseIntervalMinutes(expr: string | undefined): string {
  if (!expr) return "";
  const m = /^(\d+)\s*m?$/i.exec(expr.trim());
  return m ? m[1] : "";
}

/** BotDetail → the Bot row the rail keeps (drops the transcript). */
function botSummary(d: BotDetail): Bot {
  return {
    id: d.id,
    name: d.name,
    description: d.description,
    status: d.status,
    schedule: d.schedule,
    skill: d.skill,
    skills: d.skills,
    created_at: d.created_at,
    last_run_at: d.last_run_at,
    last_result: d.last_result,
    approvals_pending: d.approvals_pending,
    workspace: d.workspace,
  };
}

/* ---- atoms ------------------------------------------------------------------ */

function StatusDot({
  status,
  className,
}: {
  status: Bot["status"];
  className?: string;
}) {
  const meta = STATUS_META[status];
  return (
    <span
      className={cn("relative inline-flex size-2 shrink-0", className)}
      title={meta.label}
    >
      <span
        className="absolute inset-0 rounded-full"
        style={{ backgroundColor: meta.dot }}
      />
      {meta.pulse && (
        <span
          className="absolute inset-0 animate-ping rounded-full opacity-70"
          style={{ backgroundColor: meta.dot }}
        />
      )}
    </span>
  );
}

function BotAvatar({ name, size = "size-9" }: { name: string; size?: string }) {
  const letter = (name.trim()[0] ?? "?").toUpperCase();
  return (
    <div
      className={cn(
        "flex shrink-0 select-none items-center justify-center rounded-full bg-[var(--ds-surface-3)] font-semibold text-[var(--ds-ink)]",
        size
      )}
      aria-hidden="true"
    >
      {letter}
    </div>
  );
}

function Chip({
  icon: Icon,
  text,
  accent,
}: {
  icon: LucideIcon;
  text: string;
  accent?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex max-w-56 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs",
        accent
          ? "border-[rgba(70,191,114,0.3)] bg-[rgba(70,191,114,0.12)] text-[var(--ds-accent)]"
          : "border-[var(--ds-line)] bg-white/[0.03] text-muted-foreground"
      )}
    >
      <Icon className="size-3 shrink-0" />
      <span className="truncate">{text}</span>
    </span>
  );
}

function EventBubble({ ev }: { ev: BotEvent }) {
  const time = fmtClock(ev.ts);
  if (ev.role === "user") {
    return (
      <div className="flex items-end justify-end gap-1.5">
        {time && (
          <span className="mb-1 shrink-0 text-[10px] text-muted-foreground/60">
            {time}
          </span>
        )}
        <div className="max-w-[78%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-[var(--ds-surface-3)] px-3.5 py-2 text-sm text-[var(--ds-ink)]">
          {ev.text}
        </div>
      </div>
    );
  }
  if (ev.role === "bot") {
    return (
      <div className="flex items-end justify-start gap-1.5">
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-bl-md border border-[var(--ds-line)] bg-[var(--ds-surface-1)] px-3.5 py-2 text-sm text-[var(--ds-ink)]">
          {ev.text}
        </div>
        {time && (
          <span className="mb-1 shrink-0 text-[10px] text-muted-foreground/60">
            {time}
          </span>
        )}
      </div>
    );
  }
  // system / approval — centered, muted, small
  return (
    <div className="flex justify-center">
      <div className="max-w-[85%] rounded-full border border-[var(--ds-line)] bg-white/[0.02] px-3 py-1 text-center text-xs leading-relaxed text-muted-foreground">
        {ev.role === "approval" ? "Approval · " : ""}
        {ev.text}
      </div>
    </div>
  );
}

/* ---- right panel: Screen tab ------------------------------------------------ */

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
    <TabsContent
      value="screen"
      forceMount
      className="min-h-0 flex-1 data-[state=inactive]:hidden"
    >
      <div className="flex h-full min-h-0 flex-col gap-2 p-3">
        <div className="flex shrink-0 items-center gap-2">
          <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            Bot's screen — you can control the bot's machine
          </p>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Open the screen in a new window"
                disabled={!src}
                onClick={() => {
                  if (src) window.open(src, "_blank", "noopener");
                }}
              >
                <Maximize2 className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent className="border border-[var(--ds-line)] bg-[#2b2b2b] text-muted-foreground">
              Open in a new window
            </TooltipContent>
          </Tooltip>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-[var(--ds-line)] bg-black/40">
          {src ? (
            <iframe
              key={bot.id}
              src={src}
              title={`${bot.name} — live screen`}
              className="size-full"
            />
          ) : (
            <div className="flex size-full items-center justify-center">
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
          )}
        </div>
      </div>
    </TabsContent>
  );
}

/* ---- right panel: Details tab ----------------------------------------------- */

const STEPS: { icon: LucideIcon; title: string; hint: string }[] = [
  { icon: PlayCircle, title: "TASK", hint: "Run the job once and watch it" },
  { icon: Save, title: "SKILL", hint: "Save the method as a skill" },
  { icon: Clock, title: "ROUTINE", hint: "Run on a schedule" },
];

function Stepper({ bot }: { bot: Bot }) {
  const current = bot.schedule.kind !== "none" ? 2 : bot.skill ? 1 : 0;
  return (
    <section>
      <h3 className={SECTION_TITLE}>Path to a routine</h3>
      <ol className="mt-2 flex items-stretch gap-2">
        {STEPS.map((s, i) => {
          const done = i < current;
          const active = i === current;
          return (
            <li key={s.title} className="relative flex-1">
              {i > 0 && (
                <span
                  aria-hidden="true"
                  className="absolute -left-2 top-4 h-px w-2 bg-[var(--ds-line)]"
                />
              )}
              <div
                className={cn(
                  "flex h-full flex-col gap-1.5 rounded-lg border p-2.5",
                  active
                    ? "border-[rgba(70,191,114,0.4)] bg-[rgba(70,191,114,0.1)]"
                    : done
                      ? "border-[var(--ds-line)] bg-white/[0.03]"
                      : "border-[var(--ds-line)] opacity-60"
                )}
              >
                <s.icon
                  className={cn(
                    "size-4",
                    active
                      ? "text-[var(--ds-accent)]"
                      : done
                        ? "text-foreground/70"
                        : "text-muted-foreground"
                  )}
                />
                <p
                  className={cn(
                    "text-[11px] font-semibold tracking-wide",
                    active ? "text-[var(--ds-accent)]" : "text-foreground/80"
                  )}
                >
                  {s.title}
                </p>
                <p className="text-[10px] leading-snug text-muted-foreground">
                  {s.hint}
                </p>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function DetailsTab({
  bot,
  onChanged,
  onDelete,
}: {
  bot: BotDetail;
  onChanged: () => void;
  onDelete: () => Promise<void>;
}) {
  // save last run as a skill
  const [skillName, setSkillName] = useState("");
  const [savingSkill, setSavingSkill] = useState(false);

  // schedule editor draft — reset on bot switch or poll sync, but never
  // clobbered while the user has an edit in flight (dirty)
  const [schedDraft, setSchedDraft] = useState<BotSchedule>(bot.schedule);
  const [dirty, setDirty] = useState(false);
  const [savingSched, setSavingSched] = useState(false);
  const [draftOwner, setDraftOwner] = useState(bot.id);
  const [seenSched, setSeenSched] = useState(() =>
    JSON.stringify(bot.schedule)
  );
  const nextSched = JSON.stringify(bot.schedule);
  if (draftOwner !== bot.id || (nextSched !== seenSched && !dirty)) {
    setDraftOwner(bot.id);
    setSeenSched(nextSched);
    setSchedDraft(bot.schedule);
    setDirty(false);
  }

  // delete confirmation
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const minutes = parseIntervalMinutes(schedDraft.expr);
  const schedValid =
    schedDraft.kind === "none" ||
    (schedDraft.kind === "daily" && !!schedDraft.time) ||
    (schedDraft.kind === "interval" &&
      minutes !== "" &&
      Number(minutes) > 0) ||
    (schedDraft.kind === "cron" && !!schedDraft.expr?.trim());

  const updateSched = (s: BotSchedule) => {
    setSchedDraft(s);
    setDirty(true);
  };

  const saveSkill = async () => {
    const name = skillName.trim().replace(/^\/+/, "");
    if (!name || savingSkill) return;
    setSavingSkill(true);
    try {
      const r = await setBotSkill(bot.id, name);
      setSkillName("");
      toast.success("Saved as a skill", { description: `/${r.skill}` });
      onChanged();
    } catch (e) {
      toast.error("Could not save the skill", { description: errText(e) });
    } finally {
      setSavingSkill(false);
    }
  };

  const buildSchedulePayload = (): BotSchedule => {
    switch (schedDraft.kind) {
      case "daily":
        return {
          kind: "daily",
          time: schedDraft.time || undefined,
          tz: schedDraft.tz?.trim() || undefined,
        };
      case "interval":
        return { kind: "interval", expr: `${minutes}m` };
      case "cron":
        return { kind: "cron", expr: schedDraft.expr?.trim() || undefined };
      default:
        return { kind: "none" };
    }
  };

  const saveSched = async () => {
    if (!schedValid || savingSched) return;
    setSavingSched(true);
    try {
      const saved = await setBotSchedule(bot.id, buildSchedulePayload());
      setSchedDraft(saved.schedule);
      setSeenSched(JSON.stringify(saved.schedule));
      setDirty(false);
      toast.success("Schedule saved", {
        description: describeSchedule(saved.schedule),
      });
      onChanged();
    } catch (e) {
      toast.error("Could not save the schedule", { description: errText(e) });
    } finally {
      setSavingSched(false);
    }
  };

  const clearSched = async () => {
    if (savingSched) return;
    setSavingSched(true);
    try {
      const saved = await clearBotSchedule(bot.id);
      setSchedDraft(saved.schedule);
      setSeenSched(JSON.stringify(saved.schedule));
      setDirty(false);
      toast.success("Schedule cleared", { description: bot.name });
      onChanged();
    } catch (e) {
      toast.error("Could not clear the schedule", { description: errText(e) });
    } finally {
      setSavingSched(false);
    }
  };

  const remove = async () => {
    if (deleting) return;
    setDeleting(true);
    try {
      await onDelete();
      setConfirmDelete(false);
    } catch (e) {
      toast.error("Could not delete the bot", { description: errText(e) });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <TabsContent
      value="details"
      forceMount
      className="min-h-0 flex-1 data-[state=inactive]:hidden"
    >
      <ScrollArea className="h-full">
        <div className="flex flex-col gap-4 p-4 text-sm">
          {/* identity — read-only */}
          <section>
            <h3 className={SECTION_TITLE}>Bot</h3>
            <p className="mt-1.5 font-medium text-foreground">{bot.name}</p>
            <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-muted-foreground">
              {bot.description || "No description."}
            </p>
            <p
              className="mt-2 truncate font-mono text-[11px] text-muted-foreground"
              title={bot.workspace}
            >
              {bot.workspace}
            </p>
            {bot.skill && (
              <p className="mt-1 text-[11px] text-muted-foreground">
                Active skill:{" "}
                <span className="font-mono text-[var(--ds-accent)]">/{bot.skill}</span>
              </p>
            )}
          </section>

          <Separator className="bg-[var(--ds-line)]" />

          <Stepper bot={bot} />

          <Separator className="bg-[var(--ds-line)]" />

          {/* save last run as skill */}
          <section>
            <h3 className={SECTION_TITLE}>Save last run as skill</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Name the method the bot just figured out — it becomes a reusable
              Orbit.
            </p>
            <div className="mt-2 flex gap-2">
              <Input
                value={skillName}
                onChange={e => setSkillName(e.target.value)}
                maxLength={64}
                placeholder="daily-blog-pulse"
                className={FIELD}
                onKeyDown={e => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void saveSkill();
                  }
                }}
              />
              <Button
                size="sm"
                className={cn("h-8 shrink-0 px-3", ACCENT_BTN)}
                disabled={!skillName.trim() || savingSkill}
                onClick={() => void saveSkill()}
              >
                {savingSkill ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Save className="size-3.5" />
                )}
                Save
              </Button>
            </div>
          </section>

          <Separator className="bg-[var(--ds-line)]" />

          {/* schedule editor */}
          <section>
            <h3 className={SECTION_TITLE}>Schedule</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Turn the skill into a routine — the bot runs it on its own.
            </p>
            <div className="mt-2 flex flex-col gap-2">
              <Select
                value={schedDraft.kind}
                onValueChange={v => updateSched({ ...schedDraft, kind: v as ScheduleKind })}
              >
                <SelectTrigger className={FIELD}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="border-[var(--ds-line)] bg-[#2b2b2b]">
                  <SelectItem value="none">None</SelectItem>
                  <SelectItem value="daily">Daily</SelectItem>
                  <SelectItem value="interval">Interval</SelectItem>
                  <SelectItem value="cron">Cron</SelectItem>
                </SelectContent>
              </Select>

              {schedDraft.kind === "daily" && (
                <div className="flex gap-2">
                  <Input
                    type="time"
                    value={schedDraft.time ?? ""}
                    onChange={e =>
                      updateSched({
                        ...schedDraft,
                        kind: "daily",
                        time: e.target.value || undefined,
                      })
                    }
                    aria-label="Time of day"
                    className={FIELD}
                  />
                  <Input
                    value={schedDraft.tz ?? ""}
                    onChange={e =>
                      updateSched({
                        ...schedDraft,
                        kind: "daily",
                        tz: e.target.value,
                      })
                    }
                    placeholder="Europe/Rome"
                    aria-label="Timezone"
                    className={FIELD}
                  />
                </div>
              )}

              {schedDraft.kind === "interval" && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  every
                  <Input
                    type="number"
                    min={1}
                    value={minutes}
                    onChange={e =>
                      updateSched({
                        ...schedDraft,
                        kind: "interval",
                        expr:
                          e.target.value.trim() === ""
                            ? undefined
                            : `${e.target.value.trim()}m`,
                      })
                    }
                    aria-label="Interval minutes"
                    className="h-8 w-20 border-[var(--ds-line)] bg-white/[0.03] text-xs"
                  />
                  minutes
                </div>
              )}

              {schedDraft.kind === "cron" && (
                <Input
                  value={schedDraft.expr ?? ""}
                  onChange={e =>
                    updateSched({
                      ...schedDraft,
                      kind: "cron",
                      expr: e.target.value,
                    })
                  }
                  placeholder="*/30 * * * *"
                  aria-label="Cron expression"
                  className={cn(FIELD, "font-mono")}
                />
              )}

              <div className="flex gap-2">
                <Button
                  size="sm"
                  className={cn("h-8 flex-1", ACCENT_BTN)}
                  disabled={!schedValid || savingSched}
                  onClick={() => void saveSched()}
                >
                  {savingSched ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Clock className="size-3.5" />
                  )}
                  Save schedule
                </Button>
                {schedDraft.kind !== "none" && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 border-[var(--ds-line)] bg-transparent text-muted-foreground hover:text-foreground"
                    disabled={savingSched}
                    onClick={() => void clearSched()}
                  >
                    Clear
                  </Button>
                )}
              </div>
            </div>
          </section>

          <Separator className="bg-[var(--ds-line)]" />

          {/* danger zone */}
          <section className="rounded-lg border border-[rgba(229,72,77,0.2)] bg-[rgba(229,72,77,0.05)] p-3">
            <h3 className="text-xs font-medium text-[#ff8589]">Danger zone</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Deleting the bot removes its transcript and schedule. The
              workspace is kept.
            </p>
            <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
              <Button
                variant="outline"
                size="sm"
                className="mt-2 border-[rgba(229,72,77,0.3)] bg-transparent text-[#ff8589] hover:bg-[rgba(229,72,77,0.1)] hover:text-[#ff8589]"
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="size-3.5" />
                Delete bot
              </Button>
              <AlertDialogContent className="border-[var(--ds-line)] bg-[#2b2b2b]">
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete “{bot.name}”?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This permanently removes the bot, its transcript and its
                    schedule. This cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    className="bg-[#e5484d] text-white hover:bg-[#ff8589]"
                    disabled={deleting}
                    onClick={e => {
                      e.preventDefault();
                      void remove();
                    }}
                  >
                    {deleting ? "Deleting…" : "Delete"}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </section>
        </div>
      </ScrollArea>
    </TabsContent>
  );
}

/* ---- right panel: Library tab ------------------------------------------------ */

function LibraryTab({ bot }: { bot: BotDetail }) {
  return (
    <TabsContent
      value="library"
      forceMount
      className="min-h-0 flex-1 data-[state=inactive]:hidden"
    >
      <ScrollArea className="h-full">
        <div className="flex flex-col gap-3 p-4">
          <h3 className={SECTION_TITLE}>Skills</h3>
          <p className="text-xs leading-relaxed text-muted-foreground">
            Skills are reusable bot methods (Orbits) — saved from a run and
            replayed on any schedule.
          </p>
          {bot.skills.length === 0 ? (
            <div className="rounded-lg border border-dashed border-[var(--ds-line)] p-4 text-center text-xs text-muted-foreground">
              No skills yet — run the job once, then save it from Details.
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {bot.skills.map(s => (
                <Badge
                  key={s}
                  variant="outline"
                  className={cn(
                    "gap-1 font-mono text-xs",
                    s === bot.skill
                      ? "border-[rgba(70,191,114,0.4)] bg-[rgba(70,191,114,0.1)] text-[var(--ds-accent)]"
                      : "border-[var(--ds-line)] text-muted-foreground"
                  )}
                >
                  <Zap className="size-3" />/{s}
                </Badge>
              ))}
            </div>
          )}
          {bot.skill && (
            <p className="text-xs text-muted-foreground">
              Active:{" "}
              <span className="font-mono text-[var(--ds-accent)]">/{bot.skill}</span>
            </p>
          )}
        </div>
      </ScrollArea>
    </TabsContent>
  );
}

/* ---- dialogs ------------------------------------------------------------------ */

function CreateBotDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (bot: Bot) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
    }
  }, [open]);

  const submit = async () => {
    const n = name.trim();
    if (!n || saving) return;
    setSaving(true);
    try {
      const bot = await createBot(n, description.trim());
      toast.success("Bot created", { description: n });
      onOpenChange(false);
      onCreated(bot);
    } catch (e) {
      toast.error("Could not create the bot", { description: errText(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-[var(--ds-line)] bg-[#2b2b2b] sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New Bot</DialogTitle>
          <DialogDescription>
            One bot, one job — the name is for you; the description holds the
            job, sources and boundaries.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="bot-name">Name</Label>
            <Input
              id="bot-name"
              value={name}
              maxLength={80}
              autoFocus
              placeholder="daily-blog-pulse"
              onChange={e => setName(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void submit();
                }
              }}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="bot-description">Description</Label>
            <Textarea
              id="bot-description"
              value={description}
              maxLength={4000}
              rows={4}
              placeholder="Every morning at 05:00, pull today's tech headlines from these sources, write a short post in my voice and leave it as a draft."
              onChange={e => setDescription(e.target.value)}
              className="resize-none"
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button
            className={ACCENT_BTN}
            disabled={saving || !name.trim()}
            onClick={() => void submit()}
          >
            {saving ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Plus />
            )}
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ApprovalsDialog({
  bot,
  open,
  onOpenChange,
  onDecided,
}: {
  bot: Bot;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDecided: () => void;
}) {
  const [rows, setRows] = useState<BotApproval[] | null>(null);
  const [deciding, setDeciding] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setRows(null);
    listApprovals(bot.id)
      .then(r => {
        if (alive) setRows(r);
      })
      .catch(e => {
        if (!alive) return;
        setRows([]);
        toast.error("Could not load approvals", { description: errText(e) });
      });
    return () => {
      alive = false;
    };
  }, [open, bot.id]);

  const decide = async (a: BotApproval, decision: ApprovalDecision) => {
    if (deciding) return;
    setDeciding(a.id);
    try {
      await decideApproval(bot.id, a.id, decision);
      toast.success(decision === "deny" ? "Denied" : "Allowed", {
        description: a.summary,
      });
      // optimistic drop, then a quiet re-sync in case more arrived
      setRows(rs => (rs ? rs.filter(x => x.id !== a.id) : rs));
      void listApprovals(bot.id)
        .then(r => setRows(r))
        .catch(() => undefined);
      onDecided();
    } catch (e) {
      toast.error("Could not record the decision", {
        description: errText(e),
      });
    } finally {
      setDeciding(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-[var(--ds-line)] bg-[#2b2b2b] sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Pending approvals</DialogTitle>
          <DialogDescription>
            {bot.name} needs a decision before it can continue.
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-[55vh] pr-2">
          {rows === null ? (
            <div className="flex items-center justify-center py-10">
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
          ) : rows.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              No approvals pending — all caught up.
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              {rows.map(a => (
                <div
                  key={a.id}
                  className="rounded-lg border border-[var(--ds-line)] bg-white/[0.03] p-3"
                >
                  <div className="flex items-center gap-2">
                    <Badge
                      variant="outline"
                      className="border-[rgba(255,138,48,0.3)] bg-[rgba(255,138,48,0.1)] text-[#ff8a30]"
                    >
                      {a.kind}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {fmtStamp(a.ts)}
                    </span>
                  </div>
                  <p className="mt-2 text-sm font-medium text-foreground">
                    {a.summary}
                  </p>
                  {a.detail && (
                    <p className="mt-1 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-muted-foreground">
                      {a.detail}
                    </p>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      className={cn("px-3", ACCENT_BTN)}
                      disabled={deciding !== null}
                      onClick={() => void decide(a, "allow_once")}
                    >
                      Allow once
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="border-[rgba(70,191,114,0.4)] bg-transparent text-[var(--ds-accent)] hover:bg-[rgba(70,191,114,0.1)] hover:text-[var(--ds-accent)]"
                      disabled={deciding !== null}
                      onClick={() => void decide(a, "always_allow")}
                    >
                      Always allow
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="border-[rgba(229,72,77,0.3)] bg-transparent text-[#ff8589] hover:bg-[rgba(229,72,77,0.1)] hover:text-[#ff8589]"
                      disabled={deciding !== null}
                      onClick={() => void decide(a, "deny")}
                    >
                      Deny
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}

/* ---- the page ------------------------------------------------------------------ */

export default function BotsPage() {
  const [bots, setBots] = useState<Bot[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<BotDetail | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [approvalsOpen, setApprovalsOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(true);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  // bumping this re-arms the detail poll immediately (post-mutation nudge)
  const [pollBump, setPollBump] = useState(0);

  const detailBusy = useRef(false);
  const optimisticRef = useRef<BotEvent[]>([]);
  const prevSelected = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const refreshList = useCallback(async (silent = false) => {
    try {
      const rows = await listBots();
      setBots(rows);
      setListError(null);
    } catch (e) {
      if (!silent) setListError(errText(e));
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  // quiet 15s sweep keeps the rail's status dots honest between interactions
  useEffect(() => {
    const t = window.setInterval(() => {
      void refreshList(true);
    }, 15000);
    return () => window.clearInterval(t);
  }, [refreshList]);

  // detail poll — every 4s while a bot is selected (+ nudges after mutations)
  useEffect(() => {
    if (prevSelected.current !== selectedId) {
      prevSelected.current = selectedId;
      optimisticRef.current = [];
      setDetail(null);
    }
    if (!selectedId) return;
    let alive = true;
    const tick = async () => {
      if (detailBusy.current) return;
      detailBusy.current = true;
      try {
        const d = await getBot(selectedId);
        if (!alive) return;
        const pending = optimisticRef.current.filter(
          o => !d.transcript.some(t => t.role === "user" && t.text === o.text)
        );
        optimisticRef.current = pending;
        setDetail({ ...d, transcript: [...d.transcript, ...pending] });
        setBots(rows =>
          rows ? rows.map(b => (b.id === d.id ? botSummary(d) : b)) : rows
        );
      } catch (e) {
        if (!alive) return;
        if (e instanceof BridgeError && e.status === 404) {
          setSelectedId(null);
          setDetail(null);
          toast.error("This bot no longer exists", {
            description: "It was deleted elsewhere.",
          });
          void refreshList(true);
        }
        // other failures are transient (bridge restart) — stay quiet,
        // the next tick retries
      } finally {
        detailBusy.current = false;
      }
    };
    void tick();
    const t = window.setInterval(tick, 4000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [selectedId, pollBump, refreshList]);

  // keep the transcript pinned to the newest event
  const transcriptLen = detail?.transcript.length ?? 0;
  const lastRole = detail?.transcript.at(-1)?.role;
  const detailId = detail?.id ?? null;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [transcriptLen, lastRole, detailId]);

  const send = async () => {
    const task = draft.trim();
    if (!task || !selectedId || sending) return;
    setSending(true);
    setDraft("");
    const optimistic: BotEvent = {
      ts: new Date().toISOString(),
      role: "user",
      text: task,
    };
    optimisticRef.current = [...optimisticRef.current, optimistic];
    setDetail(d =>
      d ? { ...d, transcript: [...d.transcript, optimistic] } : d
    );
    try {
      await runBot(selectedId, task);
      setPollBump(b => b + 1);
    } catch (e) {
      toast.error("Could not start the run", { description: errText(e) });
      setDraft(prev => prev || task);
      optimisticRef.current = optimisticRef.current.filter(
        x => x !== optimistic
      );
      setDetail(d =>
        d
          ? { ...d, transcript: d.transcript.filter(x => x !== optimistic) }
          : d
      );
    } finally {
      setSending(false);
    }
  };

  const stop = async () => {
    if (!selectedId || stopping) return;
    setStopping(true);
    try {
      await stopBot(selectedId);
      toast.success("Stop requested", {
        description: "The bot will wrap up its current step.",
      });
      setPollBump(b => b + 1);
    } catch (e) {
      toast.error("Could not stop the bot", { description: errText(e) });
    } finally {
      setStopping(false);
    }
  };

  const removeBot = useCallback(async () => {
    if (!detail) throw new Error("no bot selected");
    await deleteBot(detail.id);
    toast.success("Bot deleted", { description: detail.name });
    setSelectedId(null);
    setDetail(null);
    void refreshList(true);
  }, [detail, refreshList]);

  return (
    <div className="flex h-[calc(100vh-56px)] overflow-hidden bg-[var(--canvas)] text-foreground">
      {/* ---- sidebar: the shell's left column in Bots mode. Same width and
          tokens as the workspace sidebar — only the body changes (the
          switcher stays; the bots rail replaces the task tree). ---- */}
      <aside className="flex w-[264px] shrink-0 flex-col overflow-hidden bg-[var(--ds-surface-1)]">
        <div className="zsb-ws-row">
          <WorkspaceSwitcher />
        </div>
        <div className="flex shrink-0 items-center justify-between px-4 pb-2 pt-2.5">
          <h1 className="text-sm font-semibold tracking-wide text-[var(--ds-ink)]">
            Bots
          </h1>
          <Button
            size="sm"
            className={cn("h-8 gap-1.5 px-3", ACCENT_BTN)}
            onClick={() => setCreateOpen(true)}
          >
            <Plus className="size-3.5" />
            New Bot
          </Button>
        </div>
        <ScrollArea className="min-h-0 flex-1 px-2 pb-3">
          <div className="flex flex-col gap-0.5">
            {bots === null &&
              !listError &&
              Array.from({ length: 5 }).map((_, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2.5 rounded-lg px-2.5 py-2"
                >
                  <div className="size-8 animate-pulse rounded-full bg-white/[0.06]" />
                  <div className="flex-1 space-y-1.5">
                    <div className="h-2.5 w-2/3 animate-pulse rounded bg-white/[0.06]" />
                    <div className="h-2 w-4/5 animate-pulse rounded bg-white/[0.04]" />
                  </div>
                </div>
              ))}
            {bots?.map(b => {
              const selected = selectedId === b.id;
              return (
                <button
                  key={b.id}
                  type="button"
                  onClick={() => setSelectedId(b.id)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors",
                    selected
                      ? "bg-[var(--ds-surface-3)]"
                      : "hover:bg-white/[0.045]"
                  )}
                >
                  <BotAvatar name={b.name} size="size-8" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium text-[var(--ds-ink)]">
                      {b.name}
                    </span>
                    <span
                      className="block truncate text-[11px] text-[var(--ds-ink-3)]"
                      title={b.last_result ?? undefined}
                    >
                      {b.last_result ?? "No runs yet"}
                    </span>
                  </span>
                  <StatusDot status={b.status} />
                </button>
              );
            })}
            {bots !== null && bots.length === 0 && (
              <p className="px-2 py-6 text-center text-xs leading-relaxed text-[var(--ds-ink-3)]">
                Nothing here yet — your bots will line up in this rail.
              </p>
            )}
          </div>
        </ScrollArea>
      </aside>

      {/* ---- center: the bot chat — floating pill header (Grok reference),
          transcript, composer card ---- */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="relative flex h-14 shrink-0 items-center justify-center px-4">
          {detail ? (
            <>
              {/* the floating IM pill: avatar + name + live status */}
              <div className="flex items-center gap-2.5 rounded-full border border-[var(--ds-line)] bg-[var(--ds-surface-1)] py-1.5 pl-2 pr-3.5 shadow-[0_1px_2px_rgba(0,0,0,0.4)]">
                <BotAvatar name={detail.name} size="size-7" />
                <h2 className="shrink-0 text-sm font-semibold text-[var(--ds-ink)]">
                  {detail.name}
                </h2>
                <Badge
                  variant="outline"
                  className={cn(
                    "shrink-0 gap-1.5 rounded-full",
                    STATUS_META[detail.status].badge
                  )}
                >
                  <StatusDot status={detail.status} />
                  {STATUS_META[detail.status].label}
                </Badge>
              </div>
              {/* right cluster: schedule/skill + stop + panel toggle */}
              <div className="absolute right-4 flex items-center gap-1.5">
                <Chip icon={Clock} text={describeSchedule(detail.schedule)} />
                {detail.skill && <Chip icon={Zap} text={`/${detail.skill}`} accent />}
                {(detail.status === "running" ||
                  detail.status === "waiting_approval") && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-muted-foreground hover:text-foreground"
                        disabled={stopping}
                        onClick={() => void stop()}
                      >
                        {stopping ? (
                          <Loader2 className="size-3.5 animate-spin" />
                        ) : (
                          <Square className="size-3.5" />
                        )}
                        Stop
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent className="border border-[var(--ds-line)] bg-[#2b2b2b] text-muted-foreground">
                      Ask the bot to stop after its current step
                    </TooltipContent>
                  </Tooltip>
                )}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={
                        rightOpen ? "Hide the side panel" : "Show the side panel"
                      }
                      onClick={() => setRightOpen(v => !v)}
                    >
                      <PanelRight
                        className={rightOpen ? "text-[var(--ds-ink)]" : undefined}
                      />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent className="border border-[var(--ds-line)] bg-[#2b2b2b] text-muted-foreground">
                    {rightOpen ? "Hide panel" : "Show panel"}
                  </TooltipContent>
                </Tooltip>
              </div>
            </>
          ) : (
            <h2 className="text-sm font-semibold text-muted-foreground">
              Bots
            </h2>
          )}
        </header>

        {bots === null && listError ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
            <ShieldAlert className="size-6 text-[#ff8589]" />
            <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
              {listError}
            </p>
            <Button
              variant="outline"
              className="border-[var(--ds-line)] bg-transparent hover:text-foreground"
              onClick={() => void refreshList()}
            >
              Retry
            </Button>
          </div>
        ) : bots === null ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : bots.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-5 px-6 text-center">
            <div className="flex size-16 items-center justify-center rounded-full border border-[rgba(70,191,114,0.3)] bg-[var(--ds-accent-dim)] text-[var(--ds-accent)]">
              <Sparkles className="size-7" />
            </div>
            <div className="space-y-1.5">
              <h2 className="text-lg font-semibold text-[var(--ds-ink)]">
                Create your first bot
              </h2>
              <p className="mx-auto max-w-md text-sm leading-relaxed text-muted-foreground">
                One bot, one job — give it a task once, let it master the
                method, then put it on a schedule. It runs on its own machine
                and reports back here.
              </p>
            </div>
            <Button
              size="lg"
              className={cn("px-6", ACCENT_BTN)}
              onClick={() => setCreateOpen(true)}
            >
              <Plus />
              New Bot
            </Button>
          </div>
        ) : detail ? (
          <>
            {detail.approvals_pending > 0 && (
              <div className="mx-4 mt-3 flex shrink-0 items-center gap-3 rounded-xl border border-[rgba(255,138,48,0.3)] bg-[rgba(255,138,48,0.08)] px-4 py-2.5">
                <ShieldAlert className="size-4 shrink-0 text-[#ff8a30]" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-[#ffb37a]">
                    Bot is waiting for your approval
                  </p>
                  <p className="text-xs text-[rgba(255,138,48,0.7)]">
                    {detail.approvals_pending} pending decision
                    {detail.approvals_pending === 1 ? "" : "s"} — it pauses
                    until you answer.
                  </p>
                </div>
                <Button
                  size="sm"
                  className={cn("shrink-0", ACCENT_BTN)}
                  onClick={() => setApprovalsOpen(true)}
                >
                  Review
                </Button>
              </div>
            )}

            <ScrollArea className="min-h-0 flex-1">
              <div className="flex min-h-full flex-col justify-end gap-3 p-4">
                {detail.transcript.length === 0 && (
                  <p className="mx-auto max-w-xs pt-10 text-center text-xs leading-relaxed text-muted-foreground">
                    No activity yet — describe the job below and send it. The
                    bot runs it on its machine and reports back here.
                  </p>
                )}
                {detail.transcript.map((ev, i) => (
                  <EventBubble key={i} ev={ev} />
                ))}
                <div ref={bottomRef} aria-hidden="true" />
              </div>
            </ScrollArea>

            <div className="shrink-0 p-3">
              <div className="composer flex items-end gap-2 p-2">
                <Textarea
                  value={draft}
                  onChange={e => setDraft(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void send();
                    }
                  }}
                  placeholder={`Send ${detail.name} a task…`}
                  disabled={sending}
                  rows={1}
                  className="max-h-40 min-h-9 flex-1 resize-none border-0 bg-transparent px-2 py-1.5 shadow-none focus-visible:ring-0 dark:bg-transparent md:text-sm"
                />
                <Button
                  size="icon-sm"
                  className={ACCENT_BTN}
                  aria-label="Send task"
                  disabled={sending || !draft.trim()}
                  onClick={() => void send()}
                >
                  {sending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <SendHorizonal />
                  )}
                </Button>
              </div>
            </div>
          </>
        ) : selectedId ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3">
            <Loader2 className="size-5 animate-spin text-[var(--ds-accent)]" />
            <p className="text-xs text-muted-foreground">
              Connecting to the bot…
            </p>
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
            <p className="text-sm text-muted-foreground">
              Select a bot on the left — or create a new one.
            </p>
          </div>
        )}
      </main>

      {/* ---- right panel: the bot's machine (screen / details / library) ---- */}
      {detail && (
        <section
          className={cn(
            "w-[40%] min-w-[360px] shrink-0 flex-col overflow-hidden border-l border-[var(--ds-line)] bg-[var(--ds-surface-1)]",
            rightOpen ? "flex" : "hidden"
          )}
        >
          <Tabs defaultValue="screen" className="flex min-h-0 flex-1 flex-col gap-0">
            <div className="shrink-0 border-b border-[var(--ds-line)] px-3 py-2">
              <TabsList className="h-8 w-full border border-[var(--ds-line)] bg-white/[0.03]">
                <TabsTrigger value="screen" className="h-7 text-xs">
                  Screen
                </TabsTrigger>
                <TabsTrigger value="details" className="h-7 text-xs">
                  Details
                </TabsTrigger>
                <TabsTrigger value="library" className="h-7 text-xs">
                  Library
                </TabsTrigger>
              </TabsList>
            </div>
            <ScreenTab bot={detail} />
            <DetailsTab
              bot={detail}
              onChanged={() => setPollBump(b => b + 1)}
              onDelete={removeBot}
            />
            <LibraryTab bot={detail} />
          </Tabs>
        </section>
      )}

      <CreateBotDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={bot => {
          setSelectedId(bot.id);
          setRightOpen(true);
          void refreshList(true);
        }}
      />
      {detail && (
        <ApprovalsDialog
          bot={detail}
          open={approvalsOpen}
          onOpenChange={setApprovalsOpen}
          onDecided={() => setPollBump(b => b + 1)}
        />
      )}
    </div>
  );
}
