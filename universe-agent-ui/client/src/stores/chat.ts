/**
 * stores/chat.ts — the one zustand store behind the whole desktop surface.
 *
 * State model (plan §7.3): tasks (sidebar) · messages (chat, grouped by
 * goal) · per-goal event arrays (the galactic activity feed for
 * MiniUniverse + ActivityFeed) · consent prompts (modal) ·
 * connection (banner) · settings.
 */
import { create } from "zustand";
import { toast } from "sonner";
import { api, BridgeEvent, ConsentPrompt, Goal, Settings } from "@/lib/api";
import type { WsState } from "@/lib/ws";

export type TaskStatus =
  "idle" | "running" | "completed" | "failed" | "cancelled";

export interface Task {
  turn_id: string;
  text: string;
  status: TaskStatus;
  created_at: number;
  /** the task's own title (rename); Universe rows + the TopBar show it */
  title?: string;
  summary?: string;
  success?: boolean;
  error?: string;
  /** set when the goal was rejected because no LLM provider is configured */
  needsProvider?: boolean;
}

export interface ChatMessage {
  id: string;
  turn_id: string;
  role: "user" | "agent";
  text: string;
  streaming?: boolean;
  /** set when this user message course-corrected a RUNNING goal (M3) */
  steered?: boolean;
  /** unix seconds — message timestamps (Universe rows); absent on legacy
   *  threads the server never dated */
  ts?: number;
}

/** ask_user question in flight (Phase 1 broker; surfaced Phase 2) */
export interface PendingQuestion {
  pending_id: string;
  question: string;
  options: string[];
  allow_free_text: boolean;
  turn_id: string | null;
  ts: number;
}

/** Universe permission modes — see security/capability.py PERMISSION_MODES. */
export type PermissionMode = "plan" | "edit" | "ask" | "full";

interface ChatStore {
  // data
  tasks: Task[];
  messages: ChatMessage[];
  eventsByGoal: Record<string, BridgeEvent[]>;
  lastSeqByGoal: Record<string, number>;
  consents: ConsentPrompt[];
  /** ask_user questions in flight (Phase 1 broker) */
  questions: PendingQuestion[];
  settings: Settings | null;
  /** the live permission mode (plan/build/edit/yolo) — mirrored
   *  from the bridge
   *  so the composer's mode chip and the gate stay in lockstep; events
   *  keep it honest when plan.submit flips the mode mid-goal */
  permissionMode: PermissionMode;
  /** per-TASK queued follow-up count (interaction_behavior="queue") */
  queuedByTask: Record<string, number>;
  /** the project new goals start in (W6 workspace) */
  activeProject: string;
  /** the task (container) the composer is talking to. New messages
   *  continue this thread; null = the next message spawns a fresh task. */
  activeTaskId: string | null;
  /** message turn_id -> owning task id (from turn_started.task_id), so
   *  terminal events on a message resolve the sidebar TASK row. */
  taskByMessage: Record<string, string>;
  /** synchronous in-flight guard: set BEFORE any await in sendGoal so a
   *  second Enter within the network window cannot double-send (the old
   *  flow only flipped `busy` after the POST resolved — 2-4 duplicates). */
  sending: boolean;
  /** single source of provider truth: 'loading' | provider name | 'none'.
   *  Replaces the scattered mirrors (setup.markProvider, ProviderBadge
   *  30s poll, zero-state mount check, wizard-local flag) that diverged
   *  after a Settings install and showed stale "no provider" guidance. */
  providerServing: string;
  providerCheckedAt: number;
  /** the (provider, model) the universe agent is pinned to — the composer's
   *  model chip reads/writes this. Subagents inherit it (they are the same
   *  model by design), so this IS the app-wide model. */
  activeProvider: string;
  activeModel: string;

  // connection
  connection: WsState;
  backendReady: boolean;
  backendDetail: string;

  // actions
  setConnection: (s: WsState) => void;
  setBackend: (ready: boolean, detail?: string) => void;
  bootstrap: () => Promise<void>;
  refreshTasks: () => Promise<void>;
  /** start a fresh empty conversation WITHOUT creating a server task:
   *  the task row appears lazily on the first message (the bridge's
   *  auto-create path). Clicking New task twice shows no rows at all. */
  newConversation: () => void;
  /** remove a task row from live state (delete/archive); resets the view
   *  when the removed one was active/viewed so no ghost survives */
  dropTask: (turnId: string) => void;
  /** load a past task's full thread into the chat surface (?goal= view) */
  loadGoalHistory: (turnId: string) => Promise<boolean>;
  /** Edit on a sent message: cut the thread at that message goal (it and
   *  every later turn leave the thread — bridge truncate). Returns "cut"
   *  on a real cut, "legacy" when the task has no turn rows to cut, null
   *  when the cut was refused (still running / request failed). */
  truncateThread: (messageGoalId: string) => Promise<"cut" | "legacy" | null>;
  /** drop the ?goal= view and return to the live conversation */
  clearGoalView: () => void;
  /** turn_id currently pinned in the ?goal= previous-conversation view */
  viewedGoalId: string | null;
  /** Universe input bar: the send may carry text-file context paths (v1
   *  attachments) and the Max chip's thinking level. Both are optional —
   *  every existing call site is unaffected. Studio sends add the picked
   *  tool card (design | video | 3d) — the same engine, anchored tool. */
  sendGoal: (
    text: string,
    opts?: {
      attachments?: string[];
      effort?: string;
      freshTask?: boolean;
      studioTool?: "design" | "video" | "3d";
    }
  ) => Promise<string | null>;
  /** M3: course-correct a RUNNING goal — routed to the orchestrator's
   *  steer queue (applied at the next step boundary + replan). Returns
   *  false when no goal is running to steer. */
  steerMessage: (text: string) => Promise<boolean>;
  cancelGoal: (turnId: string) => Promise<boolean>;
  ingestEvent: (ev: BridgeEvent) => void;
  decideConsent: (pendingId: string, approve: boolean) => Promise<void>;
  refreshConsents: () => Promise<void>;
  /** ask_user: answer a pending question through the broker */
  answerQuestion: (pendingId: string, answer: string) => Promise<void>;
  /** re-sync questions stuck from before this surface loaded */
  refreshQuestions: () => Promise<void>;
  /** switch the permission mode (plan/build/edit/yolo) on the bridge */
  switchPermissionMode: (mode: PermissionMode) => Promise<boolean>;
  loadSettings: () => Promise<void>;
  saveSettings: (patch: Partial<Settings>) => Promise<void>;
  setActiveProject: (pid: string) => void;
  /** refresh the single provider-truth mirror from GET /api/providers */
  refreshProvider: () => Promise<void>;
  /** route EVERY agent (universe + subagents) to a provider's model — the
   *  composer's model chip is the one place models are chosen now */
  switchModel: (provider: string, model: string) => Promise<boolean>;
  /** drop a local-only failed row (e.g. local-* offline rejects the
   *  backend can never DELETE) after its server round-trip 404s */
  dropLocalTask: (turnId: string) => void;

  // internal
  _wsGoalIds(): string[];
}

const MAX_EVENTS_PER_GOAL = 600;
/** terminal-goal event logs older than this are dropped (memory bound) */
const TERMINAL_EVENTS_TTL_S = 30 * 60;

const KNOWN_STATUSES: ReadonlySet<string> = new Set([
  "running",
  "completed",
  "failed",
  "cancelled",
  // draft task containers live in the DB as 'active' (and legacy rows as
  // 'pending'/'queued'): an empty container has NOTHING running — it must
  // never read as running/failed, or an untouched task shows a Stop button
  "active",
  "pending",
  "queued",
]);

/** Wire status → client status. Draft-container states collapse to 'idle'. */
function toClientStatus(v: string): TaskStatus {
  if (v === "active" || v === "pending" || v === "queued") return "idle";
  return v as TaskStatus;
}

/** Prefer the real goal status; bridge_status wins only when it is a known
 *  lifecycle value, otherwise it is stale/unknown and the DB status stands.
 *  Draft-container states (active/pending/queued) collapse to 'idle'. */
export function resolveTaskStatus(g: {
  status?: string;
  bridge_status?: string;
}): TaskStatus {
  const pick = (v: string | undefined): TaskStatus | null =>
    v && KNOWN_STATUSES.has(v) ? toClientStatus(v) : null;
  return pick(g.bridge_status) ?? pick(g.status) ?? "failed";
}

function statusFromWire(ev: BridgeEvent): TaskStatus | null {
  switch (ev.type) {
    case "turn_completed":
    case "turn_completed": // legacy mirrors
      return "completed";
    case "turn_failed":
    case "turn_failed": // legacy mirrors
      return "failed";
    case "turn_cancelled":
    case "turn_cancelled": // legacy mirrors
      return "cancelled";
    default:
      return null;
  }
}

export const useChat = create<ChatStore>((set, get) => ({
  tasks: [],
  messages: [],
  eventsByGoal: {},
  lastSeqByGoal: {},
  consents: [],
  questions: [],
  permissionMode: "ask",
  queuedByTask: {},
  settings: null,
  activeProject:
    typeof localStorage !== "undefined"
      ? localStorage.getItem("activeProject") || ""
      : "",
  activeTaskId: null,
  taskByMessage: {},
  sending: false,
  viewedGoalId: null,
  providerServing: "loading",
  providerCheckedAt: 0,
  activeProvider: "",
  activeModel: "",

  connection: "connecting",
  backendReady: false,
  backendDetail: "",

  setConnection: s => set({ connection: s }),

  setBackend: (ready, detail = "") =>
    set({ backendReady: ready, backendDetail: detail }),

  setActiveProject: pid => {
    try {
      localStorage.setItem("activeProject", pid || "");
    } catch {}
    set({ activeProject: pid });
  },

  refreshProvider: async () => {
    try {
      const r = await api.getProviders();
      // the bridge reports currently_serving:'none' (or omits it) when no
      // usable key exists; has_provider is the authoritative gate signal
      const serving = r.has_provider
        ? r.currently_serving || r.providers[0]?.name || "ready"
        : "none";
      set({
        providerServing: serving,
        providerCheckedAt: Date.now(),
        activeProvider: r.active_provider || r.providers[0]?.name || "",
        activeModel: r.active_model || "",
      });
    } catch {
      // offline: keep the previous mirror; the banner already explains
    }
  },

  switchModel: async (provider, model) => {
    const m = model.trim();
    if (!provider || !m) return false;
    try {
      const r = await api.switchProviderModel(provider, m);
      set({ activeProvider: r.provider, activeModel: r.model });
      toast.success(`${r.provider} / ${r.model}`, {
        description: `one model for the main agent and all ${r.agents_routed.length - 1} subagents`,
      });
      return true;
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Model switch failed");
      return false;
    }
  },

  dropLocalTask: turnId =>
    set(s => ({
      tasks: s.tasks.filter(t => t.turn_id !== turnId),
      messages: s.messages.filter(m => m.turn_id !== turnId),
    })),

  bootstrap: async () => {
    try {
      const health = await api.health();
      get().setBackend(health.ready, health.error ?? "");
    } catch {
      get().setBackend(false, "bridge unreachable");
    }
    await get().refreshTasks();
    // stuck consents from before this page loaded (bridge restart, second
    // tab) would otherwise sit undecided until the next live event
    await get().refreshConsents();
    await get().refreshQuestions();
    // settings on EVERY app entry (reloads included): the composer chips
    // (permission mode mirror, attachments flag, default thinking level)
    // and the settings page read straight from the store
    // loadSettings never throws (offline: it keeps previous settings) —
    // so this ordering never breaks boot; the settings-dependent chips
    // land a tick later once the fetch resolves
    void get().loadSettings();
    // the mode chip mirror (best-effort: the bridge is up at bootstrap)
    void api
      .getPermissionMode()
      .then(r => set({ permissionMode: r.mode as ChatStore["permissionMode"] }))
      .catch(() => {
        /* offline: the chip stays on its default until an event corrects */
      });
  },

  refreshConsents: async () => {
    try {
      const { pending } = await api.pendingConsents();
      set(s => {
        const known = new Set(s.consents.map(c => c.pending_id));
        const fresh = pending.filter(p => !known.has(p.pending_id));
        return fresh.length ? { consents: [...s.consents, ...fresh] } : {};
      });
    } catch {
      // offline: keep what we have; the banner already explains
    }
  },

  refreshQuestions: async () => {
    try {
      const { pending } = await api.pendingQuestions();
      set(s => {
        const known = new Set(s.questions.map(q => q.pending_id));
        const fresh = pending
          .filter(p => !known.has(p.pending_id))
          .map(p => ({
            pending_id: p.pending_id,
            question: p.question,
            options: p.options ?? [],
            allow_free_text: !!p.allow_free_text,
            turn_id: p.turn_id ?? null,
            ts: Date.now() / 1000,
          }));
        return fresh.length ? { questions: [...s.questions, ...fresh] } : {};
      });
    } catch {
      // offline: questions time out broker-side; nothing to recover here
    }
  },

  answerQuestion: async (pendingId, answer) => {
    set(s => ({
      questions: s.questions.filter(q => q.pending_id !== pendingId),
    }));
    try {
      await api.answerQuestion(pendingId, answer);
    } catch {
      // already answered (timeout race) — gone from the surface either way
    }
  },

  switchPermissionMode: async mode => {
    try {
      const r = await api.setPermissionMode(mode);
      set({ permissionMode: r.mode as ChatStore["permissionMode"] });
      return true;
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "mode switch failed");
      return false;
    }
  },

  refreshTasks: async () => {
    try {
      // project-scoped like the sidebar list: the global tasks array and
      // the visible chats diverged after every project switch
      const { goals } = await api.goals(200, get().activeProject);
      const nowS = Date.now() / 1000;
      const tasks: Task[] = goals.map(g => {
        // a goal row exists only after run_goal starts; if the row exists
        // but our live state knows better (bridge restart case), prefer live
        const live = get().tasks.find(t => t.turn_id === g.turn_id);
        const rowStatus = resolveTaskStatus(g);
        const status: TaskStatus =
          live && rowStatus === "running" ? live.status : rowStatus;
        return {
          turn_id: g.turn_id,
          text: g.text,
          status,
          created_at: g.created_at ?? Date.now() / 1000,
          title: (g.title ?? undefined) || undefined,
          success: g.success,
        };
      });
      // merge: keep live tasks the DB doesn't know yet (fresh POST), and
      // local failed entries (offline rejects) — both are dropped by the DB
      // listing and must survive a refresh/project switch, not vanish
      const known = new Set(tasks.map(t => t.turn_id));
      for (const t of get().tasks) {
        if (
          !known.has(t.turn_id) &&
          (t.status === "running" || t.turn_id.startsWith("local-"))
        ) {
          tasks.unshift({ ...t });
        }
      }
      // reconcile: a task we track as running whose row is now terminal, with
      // no terminal WS event seen (bridge restart / missed event) — terminate
      // its bubble from the goal log, taking endTs from completed_at
      const wasRunning = new Set(
        get()
          .tasks.filter(t => t.status === "running")
          .map(t => t.turn_id)
      );
      for (const t of tasks) {
        if (
          wasRunning.has(t.turn_id) &&
          t.status !== "running" &&
          get().messages.some(m => m.turn_id === t.turn_id && m.streaming)
        ) {
          finalizeGoalFromLog(t.turn_id, null);
        }
      }
      set(s => {
        // prune event logs of long-terminal goals (memory bound; the cap
        // above only limits per-goal length, not how many goals we retain)
        const eventsByGoal = { ...s.eventsByGoal };
        const lastSeqByGoal = { ...s.lastSeqByGoal };
        const terminal = new Set(
          tasks.filter(t => t.status !== "running").map(t => t.turn_id)
        );
        for (const gid of Object.keys(eventsByGoal)) {
          if (!terminal.has(gid)) continue;
          const evs = eventsByGoal[gid];
          const last = evs.length ? evs[evs.length - 1].ts : 0;
          if (last && nowS - last > TERMINAL_EVENTS_TTL_S) {
            delete eventsByGoal[gid];
            delete lastSeqByGoal[gid];
          }
        }
        return { tasks, eventsByGoal, lastSeqByGoal };
      });
    } catch {
      // offline: keep what we have; the banner already explains
    }
  },

  loadGoalHistory: async turnId => {
    // previous-conversation view (?goal=): fetch the TASK record + its full
    // message thread so the whole conversation renders, not one exchange.
    // Returns false when the task can't be loaded.
    try {
      const [g, refill] = await Promise.all([
        api.goal(turnId),
        // the task container has no ring of its own; its running message
        // goal does — refill the LAST message's ring when present
        api.refill(turnId, 0).catch(() => null),
      ]);
      const status = resolveTaskStatus(g);
      const task: Task = {
        turn_id: g.turn_id,
        text: g.text,
        status,
        created_at: g.created_at ?? Date.now() / 1000,
        title: (g.title ?? undefined) || undefined,
        success: g.success,
        error: g.error ?? undefined,
      };
      // thread: server-built message list; legacy single-goal rows fall
      // back to the old one-pair shape so old chats still open
      // collect persisted tool_history + todos + lifecycle from all agent messages for replay
      const historyByGoal = new Map<string, BridgeEvent[]>();
      const allHistoryEvents: BridgeEvent[] = [];
      for (const m of g.messages ?? []) {
        const ext = m as unknown as {
          tool_history?: Array<{
            tool: string;
            ok: boolean;
            args: Record<string, unknown>;
            result_preview: string;
            seq: number;
            agent?: string;
            duration_ms?: number;
            ts?: number;
          }>;
          todos?: Array<{ content: string; status: string }>;
          todos_idx?: number | null;
          started_at?: number;
          completed_at?: number;
          elapsed_ms?: number;
        };
        const th = ext.tool_history;
        const started = ext.started_at ?? m.created_at ?? Date.now() / 1000;
        const completed =
          ext.completed_at ??
          (ext.elapsed_ms
            ? started + ext.elapsed_ms / 1000
            : started + (th?.length ? th.length * 0.5 : 1));
        const arr: BridgeEvent[] = [];
        let seq = 0;
        if (m.role === "agent") {
          seq += 1;
          arr.push({
            seq,
            type: "turn_started" as const,
            ts: started,
            turn_id: m.turn_id,
            source: "history",
          } as unknown as BridgeEvent);
          if (ext.todos?.length) {
            seq += 1;
            arr.push({
              seq,
              type: "todo_updated" as const,
              ts: started + 0.1,
              todos: ext.todos,
              in_progress_idx: ext.todos_idx ?? null,
              turn_id: m.turn_id,
              source: "history",
            } as unknown as BridgeEvent);
          }
        }
        if (th?.length) {
          const span = Math.max(0.1, completed - started);
          const perTool = span / th.length;
          for (let i = 0; i < th.length; i++) {
            const h = th[i];
            seq += 1;
            const ts =
              (h as unknown as { ts?: number }).ts ??
              started + perTool * (i + 0.5);
            arr.push({
              seq,
              type: h.ok
                ? ("tool_finished" as const)
                : ("tool_failed" as const),
              tool: h.tool,
              args: h.args,
              result_preview: h.result_preview,
              agent: (h as unknown as { agent?: string }).agent ?? "universe",
              duration_ms:
                (h as unknown as { duration_ms?: number }).duration_ms ?? 0,
              turn_id: m.turn_id,
              ts,
              source: "history",
            } as unknown as BridgeEvent);
          }
        }
        if (m.role === "agent") {
          seq += 1;
          const termType =
            (m as unknown as { success?: boolean }).success === false
              ? ("turn_failed" as const)
              : ("turn_completed" as const);
          arr.push({
            seq,
            type: termType,
            ts: completed,
            turn_id: m.turn_id,
            source: "history",
          } as unknown as BridgeEvent);
        }
        if (arr.length) {
          historyByGoal.set(m.turn_id, arr);
          allHistoryEvents.push(...arr);
        }
      }
      const thread: ChatMessage[] = (g.messages ?? []).map((m, i) => ({
        id: `${m.role === "user" ? "u" : "a"}-${m.turn_id}-${i}`,
        turn_id: m.turn_id,
        role: m.role,
        text: m.text ?? "",
        streaming: false,
        ts: m.created_at ?? undefined,
      }));
      if (!thread.length) {
        const body = summarizeGoal(g);
        thread.push(
          {
            id: `u-${turnId}`,
            turn_id: turnId,
            role: "user",
            text: g.text,
          },
          {
            id: `a-${turnId}`,
            turn_id: turnId,
            role: "agent",
            streaming: false,
            text: body,
          }
        );
      } else if (
        status === "running" &&
        !thread.some(m => m.role === "agent")
      ) {
        // resync mid-run: the thread has no agent turn yet (the bridge only
        // writes final_summary at the end) - append a streaming agent turn
        // so the live ring feeds the flow rows right away, exactly like a
        // conversation started while this app was connected
        thread.push({
          id: `a-${turnId}`,
          turn_id: turnId,
          role: "agent",
          streaming: true,
          text: "",
        });
      }
      // Reopen replay: the refill now returns the goal's FULL durable wire
      // stream (bridge ~/.universe/rings — the task ring plus every message
      // ring merged). Distribute the events by each one's own turn_id so
      // every agent message's timeline restores at full fidelity (thinking,
      // plan, tools, subagents — everything the live flow showed), then
      // mirror a seq-stable aggregate under the task id for
      // ActivityPane/StatusCard. The tool_history rebuild below stays as
      // the fallback for goals that never got a ring file.
      const refillEvents = [...(refill?.events ?? [])].sort(
        (a, b) =>
          ((a as { ts?: number }).ts ?? 0) -
            ((b as { ts?: number }).ts ?? 0) ||
          (a.seq ?? 0) - (b.seq ?? 0)
      );
      const refillGroups = new Map<string, BridgeEvent[]>();
      for (const e of refillEvents) {
        const gid = (e as { turn_id?: string }).turn_id || turnId;
        const arr = refillGroups.get(gid);
        if (arr) arr.push(e);
        else refillGroups.set(gid, [e]);
      }
      set(s => {
        const known = new Set(s.tasks.map(t => t.turn_id));
        const nextEvents = { ...s.eventsByGoal };
        const nextSeqs = { ...s.lastSeqByGoal };
        if (refillEvents.length) {
          for (const [gid, evs] of Array.from(refillGroups.entries())) {
            // a live ring already in the store is richer — never downgrade it
            if (nextEvents[gid]?.length) continue;
            nextEvents[gid] = evs;
            nextSeqs[gid] = evs[evs.length - 1]?.seq ?? evs.length;
          }
          if (!nextEvents[turnId]?.length) {
            // aggregate under the task id, from CLONES: the per-message
            // events keep their own seqs (live dedup depends on them)
            const agg = refillEvents.map((e, i) => ({ ...e, seq: i + 1 }));
            nextEvents[turnId] = agg;
            nextSeqs[turnId] = agg.length;
          }
        }
        // History replay: distribute per-message events + aggregate under taskId for ActivityPane/StatusCard
        for (const [mid, evs] of Array.from(historyByGoal.entries())) {
          if (!nextEvents[mid] || nextEvents[mid].length === 0) {
            nextEvents[mid] = (evs as BridgeEvent[]).sort(
              (a: BridgeEvent, b: BridgeEvent) => (a.seq ?? 0) - (b.seq ?? 0)
            );
            nextSeqs[mid] = evs[evs.length - 1]?.seq ?? evs.length;
          }
        }
        if (
          allHistoryEvents.length &&
          (!nextEvents[turnId] || nextEvents[turnId].length === 0)
        ) {
          const sorted = [...allHistoryEvents].sort(
            (a: BridgeEvent, b: BridgeEvent) => (a.seq ?? 0) - (b.seq ?? 0)
          );
          // re-seq for aggregate view
          sorted.forEach((e, i) => {
            (e as unknown as { seq: number }).seq = i + 1;
          });
          nextEvents[turnId] = sorted;
          nextSeqs[turnId] = sorted.length;
        }
        return {
          viewedGoalId: turnId,
          activeTaskId: turnId,
          tasks: known.has(turnId)
            ? s.tasks.map(t => (t.turn_id === turnId ? task : t))
            : [task, ...s.tasks],
          messages: thread,
          eventsByGoal: nextEvents,
          lastSeqByGoal: nextSeqs,
        };
      });
      return true;
    } catch {
      return false;
    }
  },

  clearGoalView: () => set({ viewedGoalId: null }),

  truncateThread: async messageGoalId => {
    const taskId = get().viewedGoalId ?? get().activeTaskId;
    if (!taskId) return null;
    const idx = get().messages.findIndex(m => m.turn_id === messageGoalId);
    if (idx === -1) return null;
    const dead = new Set(
      get()
        .messages.slice(idx)
        .map(m => m.turn_id)
    );
    const r = await api
      .truncateMessages(taskId, messageGoalId)
      .catch(() => null);
    if (!r) return null;
    if (r.legacy) return "legacy";
    if (!r.removed) return null;
    set(s => ({
      messages: s.messages.slice(0, idx),
      eventsByGoal: Object.fromEntries(
        Object.entries(s.eventsByGoal).filter(([k]) => !dead.has(k))
      ),
      lastSeqByGoal: Object.fromEntries(
        Object.entries(s.lastSeqByGoal).filter(([k]) => !dead.has(k))
      ),
    }));
    void get().refreshTasks();
    return "cut";
  },

  newConversation: () => {
    // LAZY: nothing is created server-side and nothing appears in the
    // sidebar. The UI is simply reset to the empty composer state; the
    // first message sends POST /api/turn WITHOUT turn_id and the bridge
    // auto-creates the container (titled "New task"). Pressing the button
    // repeatedly — or before any message — must never mint rows.
    set({
      activeTaskId: null,
      viewedGoalId: null,
      messages: [],
      eventsByGoal: {},
      lastSeqByGoal: {},
    });
  },

  dropTask: turnId => {
    // a deleted/archived row must not survive in live state: Composer's
    // running-scoping and loadChats' live-merge both read s.tasks, and a
    // locally-"running" row for a server-side-deleted task re-appeared as
    // a ghost "New task" on the next refresh.
    set(s => {
      const wasViewed = s.activeTaskId === turnId || s.viewedGoalId === turnId;
      const url = new URL(window.location.href);
      const hadParam = url.searchParams.has("goal");
      if (wasViewed && hadParam) {
        url.searchParams.delete("goal");
        window.history.replaceState(
          null,
          "",
          url.pathname + url.search + url.hash
        );
      }
      return {
        tasks: s.tasks.filter(t => t.turn_id !== turnId),
        // a deleted task's queued follow-ups die with it server-side —
        // never leave a phantom "N queued" indicator behind
        queuedByTask: Object.fromEntries(
          Object.entries(s.queuedByTask).filter(([k]) => k !== turnId)
        ),
        ...(wasViewed
          ? {
              activeTaskId: null,
              viewedGoalId: null,
              messages: [],
              eventsByGoal: {},
              lastSeqByGoal: {},
            }
          : {}),
      };
    });
  },

  sendGoal: async (text, opts) => {
    const trimmed = text.trim();
    if (!trimmed || get().sending) return null;
    // the in-flight lock goes up SYNCHRONOUSLY, before the first await:
    // the old flow kept `busy` false until the POST resolved, so a second
    // Enter in the network window duplicated the goal 2-4x
    set({ sending: true });
    const release = () => set({ sending: false });
    // NO pre-send provider roundtrip: the bridge's own preflight rejects
    // with code:"no_provider" in one request when truly unconfigured, and
    // the blocking GET here made the FIRST send of every session wait for
    // a second network hop before the message even left. The mirror is
    // refreshed in the background for the badge only.
    void get()
      .refreshProvider()
      .catch(() => {});
    // response_language lives in settings; if the bridge answered since
    // boot they are loaded, otherwise pull them in the background (non-
    // blocking) so a later send honors the preference
    if (!get().settings)
      void get()
        .loadSettings()
        .catch(() => {});
    // continuation: messages go into the ACTIVE task; without one the
    // bridge auto-creates the container ("New task", never message text).
    // A freshTask send (new-task button) overrides this: it is its own
    // workstream — it ALWAYS mints a fresh task, never rides an open one
    const taskCtx = opts?.freshTask ? null : get().activeTaskId;
    // attachments ride STRUCTURED (the bridge validates the paths and
    // merges them into one fenced block beside the agent's message); the
    // old composed-in-text v1 is retired — it burned the 8000-char
    // request budget and glued media paths into every visible bubble
    const sendText = trimmed;
    // balanced is the wire default — omit it entirely
    const effort =
      opts?.effort === "low" || opts?.effort === "high"
        ? opts.effort
        : undefined;
    let turnId: string;
    let taskId: string;
    try {
      // the user's Settings → General → Response language preference;
      // "" = auto-match (send "en" as the conservative default — the
      // classifier instructs replying in the user's language anyway)
      const lang =
        (get().settings?.response_language ?? "").trim().toLowerCase() || "en";
      const res = await api.startGoal(sendText, lang, get().activeProject, {
        turnId: taskCtx ?? undefined,
        clientMsgId: `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        effort,
        attachments: opts?.attachments,
        studioTool: opts?.studioTool,
      });
      // interaction_behavior="queue": the bridge buffered this follow-up
      // behind the running goal (turn_id null). No bubble yet — it renders
      // when the drain dispatches it (turn_started creates it below); here
      // we only feed the composer's "N queued" indicator. The return is the
      // TASK id — a non-null sentinel so the composer CLEARS the input
      // (returning null here read as the error path and left stale text,
      // inviting a double-queue on the next Enter).
      if (res.status === "queued") {
        const tid = res.task_id ?? taskCtx ?? "";
        if (tid)
          set(s => ({
            queuedByTask: {
              ...s.queuedByTask,
              [tid]: (s.queuedByTask[tid] ?? 0) + 1,
            },
          }));
        release();
        return tid || "queued";
      }
      turnId = res.turn_id;
      taskId = res.task_id ?? taskCtx ?? turnId;
    } catch (err) {
      release();
      // surface as a failed task + an honest chat reply so the recovery
      // path is visible right where the user typed
      const code = (err as { code?: string }).code;
      let message = err instanceof Error ? err.message : "failed to start goal";
      if (code === "no_provider") {
        message =
          "No LLM provider is configured. Open Settings → Model provider and connect one.";
      }
      const gid = `local-${Date.now()}`;
      const now0 = Date.now() / 1000;
      set(s => ({
        messages: [
          ...s.messages,
          {
            id: `u-${gid}`,
            turn_id: gid,
            role: "user" as const,
            text: trimmed,
            ts: now0,
          },
          {
            id: `a-${gid}`,
            turn_id: gid,
            role: "agent" as const,
            streaming: false,
            ts: now0,
            text:
              code === "no_provider"
                ? `⚠ ${message}\n\nUniverse has no built-in model — connect one and send this again.\nA free-tier key (Groq, Google AI Studio, OpenRouter, Mistral, Cerebras, NVIDIA NIM) or a local server (Ollama, LM Studio, vLLM) both work.`
                : `⚠ ${message}`,
          },
        ],
      }));
      return null;
    }
    const now = Date.now() / 1000;
    set(s => ({
      // the sidebar shows TASK rows: a new container appears only when
      // this message opened a fresh task — never per message
      tasks:
        taskCtx === taskId
          ? s.tasks.map(t =>
              t.turn_id === taskId ? { ...t, status: "running" } : t
            )
          : [
              {
                turn_id: taskId,
                text: "",
                status: "running",
                created_at: now,
              },
              ...s.tasks,
            ],
      activeTaskId: taskId,
      // idempotent bubbles: the bridge schedules _run() before the HTTP
      // response returns, so turn_started can beat this POST back and the
      // ingest path has already minted the same-keyed pair — appending
      // blind would double the bubbles (duplicate React keys) and mint a
      // phantom sidebar row for the message-goal id
      messages: s.messages.some(m => m.turn_id === turnId)
        ? s.messages
        : [
            ...s.messages,
            {
              id: `u-${turnId}`,
              turn_id: turnId,
              role: "user",
              text: trimmed,
              ts: now,
            },
            {
              id: `a-${turnId}`,
              turn_id: turnId,
              role: "agent",
              text: "",
              streaming: true,
              ts: now,
            },
          ],
      // idempotent: events may already be flowing from the WS (turn_started
      // can beat the POST response back to the store)
      eventsByGoal: {
        ...s.eventsByGoal,
        [turnId]: s.eventsByGoal[turnId] ?? [],
      },
    }));
    release();
    return turnId;
  },

  steerMessage: async text => {
    const trimmed = text.trim();
    if (!trimmed) return false;
    // the steer must reach the orchestrator's run for the RUNNING MESSAGE
    // goal — the streaming agent bubble is that goal; the task row id is a
    // different thing entirely
    const live = get().messages.find(m => m.role === "agent" && m.streaming);
    const turnId = live?.turn_id ?? null;
    if (!turnId) return false;
    const bubbleId = `u-steer-${Date.now()}`;
    set(s => ({
      messages: [
        ...s.messages,
        {
          id: bubbleId,
          turn_id: turnId,
          role: "user" as const,
          text: trimmed,
          steered: true,
        },
      ],
    }));
    try {
      await api.steerGoal(turnId, trimmed);
      return true;
    } catch {
      // honest failure: the bubble stays but says it never landed
      set(s => ({
        messages: s.messages.map(m =>
          m.id === bubbleId
            ? {
                ...m,
                text: `${m.text} — ⚠ not delivered (the bridge rejected the steer)`,
              }
            : m
        ),
      }));
      return false;
    }
  },

  cancelGoal: async turnId => {
    try {
      await api.cancelGoal(turnId);
      // queue mode: the bridge drops the task's buffered follow-ups on
      // cancel (Stop must not restart work) — the client count follows
      set(s => {
        if (!(s.queuedByTask[turnId] ?? 0)) return {};
        return {
          queuedByTask: Object.fromEntries(
            Object.entries(s.queuedByTask).filter(([k]) => k !== turnId)
          ),
        };
      });
      return true;
    } catch (err) {
      // 404/409: the bridge doesn't know this goal (restart orphaned it).
      // Finalize it locally — a stuck "running" task with no way out is a
      // silent failure; the user's intent (stop) is unambiguous.
      const code = (err as { code?: string }).code;
      if (
        code === "turn_not_found" ||
        code === "turn_not_running" ||
        code === "unreachable" ||
        code === "http_404" ||
        code === "http_409"
      ) {
        set(s => ({
          tasks: s.tasks.map(t =>
            t.turn_id === turnId && t.status === "running"
              ? {
                  ...t,
                  status: "cancelled",
                  error: "goal lost in a backend restart",
                }
              : t
          ),
          messages: s.messages.map(m =>
            m.turn_id === turnId && m.role === "agent" && m.streaming
              ? {
                  ...m,
                  streaming: false,
                  text: "Stopped — the goal was lost in a backend restart.",
                }
              : m
          ),
        }));
        return true;
      }
      return false;
    }
  },

  ingestEvent: ev => {
    if (!ev.turn_id) return;
    const gid = ev.turn_id;

    const status = statusFromWire(ev);
    set(s => {
      // --- event log (dedup by seq; reconnect refills re-feed through this
      // same path, so a blind append would duplicate the overlap window)
      const evs = s.eventsByGoal[gid] ?? [];
      const seq = ev.seq ?? (s.lastSeqByGoal[gid] ?? 0) + 1;
      const incoming = { ...ev, seq };
      const merged =
        ev.seq !== undefined && evs.some(e => e.seq === ev.seq)
          ? evs
          : [...evs, incoming]
              .sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0))
              .slice(-MAX_EVENTS_PER_GOAL);
      const next: Record<string, unknown> = {
        eventsByGoal: {
          ...s.eventsByGoal,
          [gid]: merged,
        },
        lastSeqByGoal: {
          ...s.lastSeqByGoal,
          [gid]: Math.max(s.lastSeqByGoal[gid] ?? 0, seq),
        },
      };

      // --- task rollup: the bridge stamps turn_started with the owning
      // task id; terminal events on a message resolve that task row
      if (typeof ev.task_id === "string" && ev.task_id) {
        next.taskByMessage = {
          ...(s.taskByMessage as Record<string, string>),
          [gid]: ev.task_id,
        };
      }
      const ownerTask =
        (s.taskByMessage as Record<string, string> | undefined)?.[gid] ?? null;
      if (status) {
        next.tasks = s.tasks.map(t =>
          t.turn_id === gid
            ? { ...t, status }
            : ownerTask && t.turn_id === ownerTask
              ? { ...t, status }
              : t
        );
      } else if (ev.type === "turn_started" && ownerTask) {
        next.tasks = s.tasks.map(t =>
          t.turn_id === ownerTask ? { ...t, status: "running" } : t
        );
      }

      // --- live conversational reply: the bubble body IS the answer. The
      // af-chat-reply card in ActivityFeed is gone — one render, here.
      if (ev.type === "chat_reply" && typeof ev.reply === "string") {
        next.messages = s.messages.map(m =>
          m.turn_id === gid && m.role === "agent"
            ? { ...m, text: ev.reply, streaming: true }
            : m
        );
      }

      // --- consent prompts
      if (ev.type === "consent_required" && ev.pending_id) {
        const prompt: ConsentPrompt = {
          pending_id: ev.pending_id,
          tool: ev.tool ?? "",
          agent: ev.agent ?? "",
          turn_id: gid,
          args_summary: ev.args_summary ?? "",
          created_at: ev.ts,
        };
        next.consents = s.consents.some(c => c.pending_id === prompt.pending_id)
          ? s.consents
          : [...s.consents, prompt];
      }
      if (ev.type === "consent_decided" && ev.pending_id) {
        next.consents = s.consents.filter(c => c.pending_id !== ev.pending_id);
      }

      // --- ask_user questions (Phase-1 broker, surfaced Phase 2)
      if (ev.type === "question_required" && ev.pending_id) {
        const q: PendingQuestion = {
          pending_id: ev.pending_id,
          question: ev.question ?? "",
          options: ev.options ?? [],
          allow_free_text: !!ev.allow_free_text,
          turn_id: gid,
          ts: ev.ts,
        };
        if (!s.questions.some(x => x.pending_id === q.pending_id))
          next.questions = [...s.questions, q];
      }
      if (ev.type === "question_decided" && ev.pending_id) {
        next.questions = s.questions.filter(
          q => q.pending_id !== ev.pending_id
        );
      }

      // --- permission mode mirror (plan.submit flips plan→full mid-goal)
      if (ev.type === "permission_mode_changed" && ev.mode) {
        next.permissionMode = ev.mode as ChatStore["permissionMode"];
      }

      // --- queued follow-ups (interaction_behavior="queue"): the drain
      // fires the buffered messages as ordinary goals — a fresh
      // turn_started on a task with queued messages is one leaving.
      if (
        ev.type === "turn_started" &&
        typeof ev.task_id === "string" &&
        ev.task_id &&
        (s.queuedByTask[ev.task_id] ?? 0) > 0
      ) {
        next.queuedByTask = {
          ...s.queuedByTask,
          [ev.task_id]: Math.max(0, s.queuedByTask[ev.task_id] - 1),
        };
      }
      // server-dispatched goals (queue drain today; automations if they
      // ever tap the bridge bus) have no local bubbles yet — mint them
      // from the event itself so the thread the user is watching renders
      // the message the moment it runs. Two provenance caveats live here:
      // only USER-authored text mints today (start_goal + voice + queue
      // drain), and ev.text is the bridge's 2000-char event cap — a longer
      // message shows truncated until the thread is reloaded from the
      // server. If automations ever ride this path, their agent-authored
      // prompts must carry a via/source marker and never render as plain
      // user bubbles (security review, Phase 2).
      if (
        ev.type === "turn_started" &&
        typeof ev.text === "string" &&
        ev.text
      ) {
        const known = s.messages.some(m => m.turn_id === gid);
        // SCOPED to the user's eyes: a background goal (a queue drain firing
        // on another task, an automation) must not append its messages into
        // the current, unfiltered conversation — only a goal whose task is
        // the one on screen mints bubbles here
        const onScreen =
          ev.task_id === s.activeTaskId ||
          gid === s.activeTaskId ||
          gid === s.viewedGoalId;
        if (!known && onScreen) {
          next.messages = [
            ...s.messages,
            {
              id: `u-${gid}`,
              turn_id: gid,
              role: "user",
              text: ev.text,
              ts: ev.ts,
            },
            {
              id: `a-${gid}`,
              turn_id: gid,
              role: "agent",
              text: "",
              streaming: true,
              ts: ev.ts,
            },
          ];
          if (!s.tasks.some(t => t.turn_id === gid)) {
            next.tasks = [
              ...s.tasks,
              {
                turn_id: gid,
                text: ev.text,
                status: "running" as TaskStatus,
                created_at: ev.ts,
              },
            ];
          }
        }
      }

      return next as Partial<ChatStore>;
    });

    // --- agent message finalization (outside set(): needs fresh goal detail)
    if (
      ev.type === "turn_completed" ||
      ev.type === "turn_failed" ||
      ev.type === "turn_cancelled" ||
      ev.type === "turn_completed" ||
      ev.type === "turn_failed" ||
      ev.type === "turn_cancelled"
    ) {
      finalizeGoalFromLog(gid, ev.type);
    }
    // No terminal event for this goal yet, but the goal may already be over
    // (bridge restart / missed event): a chat_reply or any late event on a
    // still-"running" bubble reconciles against the goal log so the bubble
    // terminates with the log's status + endTs instead of spinning forever.
    else if (ev.type === "chat_reply") {
      const t = get().tasks.find(x => x.turn_id === gid);
      if (t?.status === "running") {
        void api
          .goal(gid)
          .then(g => {
            if (resolveTaskStatus(g) !== "running")
              finalizeGoalFromLog(gid, null, g);
          })
          .catch(() => {
            /* offline: keep the live bubble */
          });
      }
    }
  },

  decideConsent: async (pendingId, approve) => {
    set(s => ({
      consents: s.consents.filter(c => c.pending_id !== pendingId),
    }));
    try {
      await api.decideConsent(pendingId, approve);
    } catch {
      // already resolved (timeout race) — the modal is closed either way
    }
  },

  loadSettings: async () => {
    try {
      const res = await api.getSettings();
      set({ settings: res.settings });
    } catch {
      // offline: keep previous settings
    }
  },

  saveSettings: async patch => {
    // honest: failures rethrow (the caller toasts the real error), success
    // toasts only after the bridge actually persisted
    const res = await api.putSettings(patch);
    set({ settings: res.settings });
    toast.success("Settings saved");
  },

  _wsGoalIds: () => Object.keys(get().eventsByGoal),
}));

/** Terminate a goal's bubble from the goal log (not from the WS event): take
 *  status via resolveTaskStatus and endTs from the log's completed_at, and
 *  freeze the streaming agent message with the real summary. This is what
 *  ends the bubble when a goal completes without a terminal WS event.
 *  For a MESSAGE goal the refreshed body comes from final_summary.reply —
 *  never a factory report — and the owning TASK keeps its own lifecycle. */
function finalizeGoalFromLog(
  gid: string,
  evType: string | null,
  known?: Goal
): void {
  const apply = (g: Goal) => {
    const body = g.conversation && g.reply ? g.reply : summarizeGoal(g);
    useChat.setState(s => ({
      messages: s.messages.map(m =>
        m.turn_id === gid && m.role === "agent"
          ? { ...m, text: body, streaming: false }
          : m
      ),
      tasks: s.tasks.map(t => {
        if (t.turn_id === gid) {
          // the row IS this goal (legacy single-goal task)
          return {
            ...t,
            status: resolveTaskStatus(g),
            success: g.success,
            error: g.error ?? undefined,
          };
        }
        // this goal is a MESSAGE of task t: the task keeps running while
        // its thread works, but a failure must surface on the row
        const owner = (s.taskByMessage as Record<string, string>)[gid];
        if (owner && t.turn_id === owner) {
          return resolveTaskStatus(g) === "failed"
            ? { ...t, status: "failed" as const, error: g.error ?? undefined }
            : t;
        }
        return t;
      }),
    }));
  };
  if (known) {
    apply(known);
    return;
  }
  api
    .goal(gid)
    .then(apply)
    .catch(() => {
      useChat.setState(s => ({
        messages: s.messages.map(m =>
          m.turn_id === gid && m.role === "agent"
            ? {
                ...m,
                text:
                  evType === "turn_completed" || evType === "turn_completed"
                    ? "Done."
                    : "The task did not complete.",
                streaming: false,
              }
            : m
        ),
      }));
    });
}

/** Build the agent's final chat reply from the real goal record. */
function summarizeGoal(g: Goal): string {
  const lines: string[] = [];
  // a conversational reply IS the answer — never bury it under a report
  if (g.conversation && g.reply) {
    return g.reply;
  }
  if (g.success === true) {
    lines.push("✦ Task completed.");
  } else if (g.success === false) {
    // no canned "the provider kept failing / to save your quota" framing:
    // the RAW provider error below is the whole truth the user asked for
    if (!g.halted) {
      lines.push("✦ Task finished, but some acceptance criteria were not met.");
    }
  } else {
    lines.push("✦ Task ended.");
  }
  // never hide the failure reason behind a polite summary — the user
  // needs the actual error verbatim, and for a halted goal the raw
  // provider error IS the entire message (no preface at all).
  if (g.error) {
    if (!g.halted) lines.push("");
    lines.push(`Error: ${g.error}`);
    if (/no provider|no llm provider/i.test(g.error)) {
      lines.push("");
      lines.push(
        "Open Settings → Model provider and connect one — a free-tier key (Groq, Google AI Studio, OpenRouter…) or a local server (Ollama, LM Studio) both work."
      );
    }
  }
  if (g.steps?.length) {
    lines.push("");
    for (const s of g.steps) {
      const mark = s.success === false ? "✕" : "✓";
      lines.push(`${mark} ${s.agent ?? "agent"} — ${s.what ?? ""}`);
    }
  }
  if (g.artifacts?.length) {
    lines.push("");
    lines.push(`Artifacts: ${g.artifacts.length}`);
  }
  if (g.gravity_score !== undefined && g.gravity_score !== null) {
    lines.push(`Gravity score: ${g.gravity_score}`);
  }
  return lines.join("\n");
}
