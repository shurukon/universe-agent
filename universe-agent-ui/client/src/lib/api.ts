/**
 * lib/api.ts — the typed HTTP client for the Universe bridge (127.0.0.1:8738).
 *
 * Every response error is normalized to BridgeError {code, message} so the
 * UI never has to parse two shapes. Base URL + auth token resolution:
 *   1. import.meta.env.VITE_BRIDGE_URL (dev override)
 *   2. window.universe.bridgeInfo() — the Electron preload (real port+token;
 *      this is what makes a custom UNIVERSE_BRIDGE_PORT work in the renderer)
 *   3. default http://127.0.0.1:8738 with no token (dev browser mode)
 *
 * The token arrives async via IPC, so it is resolved lazily on the first
 * request and cached; every request then carries
 * `Authorization: Bearer <token>`.
 */

type UniversePreload = {
  bridgeInfo?: () => Promise<{ port: string; url: string; token: string }>;
};

let cachedUrl: string | null = null;
let cachedToken: string | null = null;
let bridgeInfoPromise: Promise<void> | null = null;

async function resolveBridgeConfig(): Promise<void> {
  if (cachedUrl !== null) return;
  const envUrl = import.meta.env.VITE_BRIDGE_URL as string | undefined;
  const preload =
    typeof window !== "undefined"
      ? (window as unknown as { universe?: UniversePreload }).universe
      : undefined;
  if (envUrl) {
    cachedUrl = envUrl;
    cachedToken = "";
    return;
  }
  if (preload?.bridgeInfo) {
    try {
      const info = await preload.bridgeInfo();
      cachedUrl = info.url || "http://127.0.0.1:8738";
      cachedToken = info.token || "";
      return;
    } catch {
      // IPC failed (non-Electron dev browser): fall through to defaults
    }
  }
  cachedUrl = "http://127.0.0.1:8738";
  cachedToken = "";
}

/** Awaitable base URL + token for the bridge (cached after first resolve). */
export async function bridgeConfig(): Promise<{ url: string; token: string }> {
  if (cachedUrl === null) {
    if (!bridgeInfoPromise) {
      bridgeInfoPromise = resolveBridgeConfig().catch(() => {
        cachedUrl = "http://127.0.0.1:8738";
        cachedToken = "";
      });
    }
    await bridgeInfoPromise;
  }
  return {
    url: cachedUrl ?? "http://127.0.0.1:8738",
    token: cachedToken || "",
  };
}

/** Sync access for the (rare) call site that runs before any request —
 *  returns the default until the async resolve completes. */
export function BRIDGE_URL(): string {
  return cachedUrl || "http://127.0.0.1:8738";
}

/** Test hook: reset the cached bridge config (e.g. after token rotation). */
export function resetBridgeConfigForTests(): void {
  cachedUrl = null;
  cachedToken = null;
  bridgeInfoPromise = null;
}

export class BridgeError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "BridgeError";
    this.code = code;
    this.status = status;
  }
}

/** Cold-start grace: Electron loads the page the instant vite answers,
 *  while the Python bridge is still spawning. First-session fetches raced
 *  it and failed with `unreachable` — on first run that silently ate the
 *  provider catalog (the wizard showed only "Custom provider"). Retry
 *  unreachable GETs for a bounded window instead of failing fast.
 *  POST/PUT/DELETE are never retried: the request may have been received
 *  and executed before the connection died (bridge restart window), so a
 *  retry could double-submit a goal or consent. */
const RETRY_DELAY_MS = 400;
const RETRY_MAX_ATTEMPTS = 25; // ≈10s — the bridge cold-starts in 2–4s
/** every request fails fast after this; GET/HEAD retries stay well inside it */
const REQUEST_TIMEOUT_MS = 30_000;

/** one attempt with an explicit timeout (zero bare fetches in src/) */
function fetchWithTimeout(
  input: string,
  init?: RequestInit
): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  const forward = init?.signal ? () => ctrl.abort() : undefined;
  if (init?.signal && forward) {
    if (init.signal.aborted) ctrl.abort();
    else init.signal.addEventListener("abort", forward, { once: true });
  }
  return fetch(input, { ...init, signal: ctrl.signal }).finally(() => {
    clearTimeout(timer);
    if (init?.signal && forward)
      init.signal.removeEventListener("abort", forward);
  });
}

async function request<T>(
  path: string,
  init?: RequestInit,
  attempt = 0
): Promise<T> {
  const { url, token } = await bridgeConfig();
  const method = (init?.method ?? "GET").toUpperCase();
  const isRetrySafe = method === "GET" || method === "HEAD";
  let res: Response;
  try {
    res = await fetchWithTimeout(`${url}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    if (isRetrySafe && attempt < RETRY_MAX_ATTEMPTS) {
      await new Promise(r => setTimeout(r, RETRY_DELAY_MS));
      return request<T>(path, init, attempt + 1);
    }
    // mutations: exactly one attempt — a retry could double-submit a goal
    // or consent that already landed before the connection died
    // fetch itself failed: bridge unreachable even after the grace window
    throw new BridgeError(
      "unreachable",
      "Universe bridge is not responding",
      0
    );
  }
  if (!res.ok) {
    let code = `http_${res.status}`;
    let message = `request failed (${res.status})`;
    try {
      const body = (await res.json()) as {
        error?: { code?: string; message?: string };
      };
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
      }
    } catch {
      /* non-JSON error body — keep the defaults */
    }
    throw new BridgeError(code, message, res.status);
  }
  return (await res.json()) as T;
}

// ---- response types --------------------------------------------------------

export interface Health {
  ok: boolean;
  version: string;
  uptime_s: number;
  ready: boolean;
  active_goals: number;
  error: string | null;
}

export interface GoalStepSummary {
  agent: string | null;
  what: string | null;
  success: boolean | null;
}

export interface Goal {
  turn_id: string;
  text: string;
  status: string;
  /** chat goals carry the direct reply instead of a step ladder */
  reply?: string;
  conversation?: boolean;
  halted?: boolean;
  created_at: number | null;
  completed_at?: number | null;
  source?: string;
  bridge_status?: string;
  success?: boolean;
  steps?: GoalStepSummary[];
  artifacts?: Array<Record<string, unknown>>;
  gravity_score?: number;
  elapsed_ms?: number;
  error?: string | null;
  // lifecycle fields (schema v14)
  title?: string | null;
  project_id?: string | null;
  session_id?: string | null;
  archived?: number | boolean;
  deleted?: number | boolean;
  pinned?: number | boolean;
  // threading (schema v17): 'task' containers vs. 'message' children
  kind?: "task" | "message";
  /** the full thread inside a task (GET /api/turn/{task}) */
  messages?: GoalThreadMessage[];
}

export interface ToolHistoryEntry {
  tool: string;
  ok: boolean;
  args: Record<string, unknown>;
  result_preview: string;
  seq: number;
}
export interface GoalThreadMessage {
  turn_id: string;
  role: "user" | "agent";
  text: string;
  status?: string;
  success?: boolean;
  steps?: GoalStepSummary[];
  tool_history?: ToolHistoryEntry[];
  created_at?: number | null;
}

export interface GoalRefill {
  turn_id: string;
  after_seq: number;
  latest_seq: number;
  events: BridgeEvent[];
}

export interface ConsentPrompt {
  pending_id: string;
  tool: string;
  capability?: string;
  agent: string;
  turn_id: string;
  args_summary: string;
  created_at: number;
}

/** one desktop.hooks[] rule (Phase 3 — editable from Settings → Hooks) */
export interface HookRule {
  /** pre_tool | pre_shell | pre_write | post_tool (post = announce-only v1) */
  event: string;
  /** require_facts (one block per goal) | block (always stop) */
  action: string;
  /** fnmatch pattern over the tool name; ""/"*" matches the whole domain */
  matcher?: string;
  /** the honest stop message shown on intercept */
  message?: string;
  /** default true — a per-rule off keeps the rule but never fires it */
  enabled?: boolean;
}

export interface Settings {
  wake_word: string;
  voice_enabled: boolean;
  stt_provider: string;
  stt_api_key: string;
  stt_model: string;
  tts_provider: string;
  tts_api_key: string;
  tts_voice: string;
  stt_cloud_model?: string;
  stt_language?: string;
  tts_model?: string;
  assistant_name?: string;
  project_root?: string;
  /** "" = match the user's message; "en" | "ar" force the answer language */
  response_language?: string;
  /** "steer" (course-correct the running goal) | "queue" (buffer follow-ups
   *  and run them when the running goal ends) — Phase 1 */
  interaction_behavior?: string;
  /** ask_user auto-continue seconds (0 = never auto-answer) */
  auto_continue_questions_s?: number;
  /* ---- Phase 3 (Settings page) — additive keys, all default on ---- */
  /** global hook kill switch: false loads zero hooks (builtins included)
   *  into the bridge's HookManager until re-enabled */
  hooks_enabled?: boolean;
  /** the user's desktop.hooks[] rule list (HookRule) */
  hooks?: HookRule[];
  /** composer "+ Add context" text attachments (v1) */
  attachments_enabled?: boolean; /** chat view: fold consecutive same-family tool rows into one group */
  group_explore_tools?: boolean;
  group_terminal_tools?: boolean;
  group_change_tools?: boolean;
  /** the composer Max chip's default for NEW messages (fast|balanced|max);
   *  a per-message pick still wins until settings change again */
  default_thinking_level?: string;
  custom_commands?: CustomCommand[];
  theme?: string;
  locale?: string;
  uiFontSize?: number;
  fontSize?: number;
  integratedTerminalShell?: string;
  terminalProfile?: string;
  terminalFontFamily?: string;
  httpProxy?: string;
  httpProxyNoProxy?: string;
  httpProxyCaCertPath?: string;
  closeToTrayOnWindows?: boolean;
  keepAwakeWhileRunning?: boolean;
  autoDownloadAndInstallUpdates?: boolean;
  taskAutoArchive?: string;
  taskAutoArchiveDays?: number;
  wrapLongLines?: boolean;
  dataBaseDir?: string;
  browserHeadless?: boolean;
  browserAllowInsecureCerts?: boolean;
  computerUseEnabled?: boolean;
  computerUseComposerEntry?: boolean;
  indexingEnabled?: boolean;
  indexingRoot?: string;
}

export interface StartGoalResponse {
  turn_id: string;
  /** the task container this message runs in (threading, schema v17) */
  task_id?: string;
  status: string;
  accepted: boolean;
  /** true when the idempotency window caught a duplicate client_msg_id */
  duplicate?: boolean;
}

// one catalog entry off /api/providers/catalog (providers/catalog.py)
export interface CatalogProvider {
  name: string;
  base_url: string;
  category: "free" | "paid" | "local";
  models: string[];
  key_url: string;
  key_optional: boolean;
  note: string;
  /** URL template placeholders to collect before install (Cloudflare:
   *  account_id, Azure: resource). The UI resolves them into base_url. */
  url_fields?: string[];
}

/** one API key row of an installed provider — masked, never the secret */
export interface ProviderKeyInfo {
  id: number;
  alias: string;
  /** masked preview: "abc…xyz" / "••••••" / "" if undecryptable */
  preview: string;
  status: string;
  /** disabled by the 5-min error-rate window (still in rotation as last
   *  resort, but the UI should flag it honestly) */
  disabled: boolean;
}

// /api/projects row (+ goal_count + git branch enrichment)
export interface Project {
  id: string;
  name: string;
  root: string;
  created_at: number | null;
  updated_at: number | null;
  archived: number;
  goal_count?: number;
  /** git branch of the project root (null when the root isn't a repo) */
  branch?: string | null;
}

// /api/mcp/servers row — transports: stdio | http (streamable) | sse
export interface McpServerInfo {
  name: string;
  transport: "stdio" | "http" | "sse";
  command: string;
  url: string;
  read_only: boolean;
  enabled: boolean;
  handshake_ok: boolean;
  tool_count: number;
  declared_hosts: string[];
}

// /api/agents row (subagents only — the universe agent is the main loop and
// is never listed; subagents inherit the one model set from the chat chip)
export interface AgentInfo {
  name: string;
  specialty: string;
  tools: string[];
  tier: string;
}

// /api/automations row (Phase C — schedule.AutomationScheduler)
export interface Automation {
  id: string;
  name: string;
  /** 5-field cron: m h dom mon dow */
  schedule: string;
  prompt: string;
  enabled: boolean;
  last_run_at: number | null;
  next_run_at: number | null;
  created_at: number;
}

// /api/skills row (Phase C — the L4 corpus inventory)
export interface SkillInfo {
  id: string;
  name: string;
  source: string;
  version: string;
  description: string;
  /** JSON-encoded string array (stored shape) */
  tags: string;
  category: string;
  status: string;
  use_count: number;
  last_used: number;
  confidence: number;
  target_agent: string;
  needs_review: boolean;
  enabled: boolean;
  solar_system_id: string;
  orbit_id: string;
}

/** the user's custom slash command (Settings → Commands; desktop.custom_commands) */
export interface CustomCommand {
  name: string;
  description: string;
  prompt: string;
}

// one event off the WS (mirrors observability/events.py + timeline wire names)
export interface BridgeEvent {
  type: string;
  ts: number;
  turn_id: string | null;
  seq?: number;
  /** turn_started — the owning task container (threading, schema v17) */
  task_id?: string;
  /** bridge_hello — the bridge's own version string */
  version?: string;
  agent?: string;
  tool?: string;
  skill?: string;
  skill_name?: string;
  solar_system?: string;
  orbit?: string;
  from_agent?: string;
  to_agent?: string;
  what_was_done?: string;
  success?: boolean;
  duration_ms?: number;
  error?: string;
  blocked_by?: string;
  pending_id?: string;
  decision?: string;
  reason?: string;
  args_summary?: string;
  detail?: string;
  plan?: Array<{ step: number; agent: string; instruction: string }>;
  round_index?: number;
  text?: string;
  /** chat_reply — the orchestrator's direct conversational answer */
  reply?: string;
  layer?: string;
  kind?: string;
  gravity_score?: number;
  topic?: string;
  domain?: string;
  outcome?: string;
  matches?: number;
  read_ok?: boolean;
  /** artifact_created (M2) — the produced file's absolute path + size */
  path?: string;
  bytes?: number;
  /** steer_received/steer_queued (M3) — the user's course-correction texts */
  messages?: string[];
  /** steer_received (M3) — the texts that triggered it */
  steer?: string[];
  /** subagent_spawned (S2) — the spawner name */
  parent?: string;
  /** subagent_spawned (S2) — the mission's depth in the spawn tree */
  depth?: number;
  /** subagent_spawned (S2) — the mission brief */
  brief?: string;
  /** subagent_complete (S2) — the mission's outcome line */
  summary?: string;

  /* ---- Phase-1 additions (events-v1.md additions log — additive only) ---- */

  /** todo_updated — the goal's live todo list + cursor */
  todos?: Array<{ content: string; status: string }>;
  in_progress_idx?: number | null;
  /** hook_intercepted — the GateGuard twin that stopped a call */
  hook_id?: string;
  event?: string;
  action?: string;
  message?: string;
  /** context_usage — the per-round token meter */
  used_tokens?: number;
  window_tokens?: number;
  window_is_estimate?: boolean;
  /** question_required / question_decided (ask_user) */
  question?: string;
  options?: string[];
  allow_free_text?: boolean;
  timeout_s?: number;
  /** permission_mode_changed — the mode chip stays live */
  mode?: string;
  previous?: string;
  /** automation_fired / automation_stalled / automation_failed */
  automation_id?: string;
  name?: string;
  schedule?: string;
  prompt?: string;
  /** steer_queued — "followup" (queue) | "dropped" (fail-safe drain) */
  queued_as?: string;
  dropped?: string;
}

// ---- endpoints ---------------------------------------------------------------

// LIVE model lists — NO client cache: the bridge pre-warms every provider
// at startup, so the request is instant and always fresh. Only in-flight
// dedup stays (two surfaces opening the same list share one request).
const modelsInflight = new Map<string, Promise<string[]>>();

async function providerModels(name: string): Promise<string[]> {
  const running = modelsInflight.get(name);
  if (running) return running;
  const p = request<{ ok: boolean; provider: string; models: string[] }>(
    `/api/providers/${encodeURIComponent(name)}/models`
  )
    .then(r => r.models)
    .finally(() => modelsInflight.delete(name));
  modelsInflight.set(name, p);
  return p;
}

/** kept for call-site compatibility: with no client cache this is a no-op
 *  — the bridge pre-warms at startup and invalidates on changes */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function invalidateModelsCache(_name?: string) {
  /* no-op: the bridge pre-warms at startup and invalidates server-side */
}

export const api = {
  health: () => request<Health>("/api/health"),
  // Round-7 CC parity readouts (/status /version /context /doctor /memory)
  statusParity: () =>
    request<{
      ok: boolean;
      version: string;
      uptime_s: number;
      ready: boolean;
      init_error: string | null;
      active_goals: number;
      permission_mode?: string;
      memory?: { systems: number; orbits: number; skills: number };
      memory_error?: string;
      permission_mode_error?: string;
    }>("/api/status-parity"),
  agentRoster: () =>
    request<{ agents: Array<{ name: string; specialty: string }> }>(
      "/api/agents"
    ),
  exportTranscript: (turnId: string) =>
    request<{ ok: boolean; path: string; messages: number }>("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turn_id: turnId }),
    }),

  // Session actions (the /fork /compact trio from the slash registry)
  branchTask: (turnId: string) =>
    request<{
      ok: boolean;
      turn_id: string;
      session_id: string;
      parent_session_id?: string;
      text: string;
    }>(`/api/turn/${encodeURIComponent(turnId)}/branch`, { method: "POST" }),
  compactTask: (turnId: string) =>
    request<{
      ok: boolean;
      compacted: boolean;
      note?: string;
      session_id?: string;
      pre_tokens?: number;
      post_tokens?: number;
    }>(`/api/turn/${encodeURIComponent(turnId)}/compact`, { method: "POST" }),

  memory: () => request<Record<string, unknown>>("/api/memory"),

  goals: (limit = 50, projectId?: string, includeArchived = false) =>
    request<{ goals: Goal[] }>(
      `/api/sessions?limit=${limit}` +
        (projectId ? `&project_id=${encodeURIComponent(projectId)}` : "") +
        (includeArchived ? "&include_archived=true" : "")
    ),

  goal: (turnId: string) => request<Goal>(`/api/turn/${turnId}`),

  refill: (turnId: string, afterSeq: number) =>
    request<GoalRefill>(`/api/turn/${turnId}/events?after_seq=${afterSeq}`),

  startGoal: (
    text: string,
    language = "en",
    projectId = "",
    opts?: {
      turnId?: string;
      clientMsgId?: string;
      /** the Max chip — maps to the per-round token budget (fast/balanced/max) */
      effort?: "low" | "medium" | "high";
      /** structured attachments: real file paths (add/paste/drop); the
       *  bridge validates + merges them into the goal text as one fenced
       *  block so the agent reads them with its own tools */
      attachments?: string[];
    }
  ) =>
    request<StartGoalResponse>("/api/turn", {
      method: "POST",
      body: JSON.stringify({
        text,
        language,
        project_id: projectId,
        ...(opts?.turnId ? { turn_id: opts.turnId } : {}),
        ...(opts?.clientMsgId ? { client_msg_id: opts.clientMsgId } : {}),
        ...(opts?.effort && opts.effort !== "medium"
          ? { effort: opts.effort }
          : {}),
        ...(opts?.attachments?.length ? { attachments: opts.attachments } : {}),
      }),
    }),

  // NOTE: the New Task flow is LAZY client-side (stores/chat.ts
  // newConversation): an empty conversation sends POST /api/turn without
  // turn_id and the bridge auto-creates the container. The
  // POST /api/tasks/draft endpoint stays on the server for other clients
  // (and stays covered by tests), but the desktop surface never mints
  // task rows on a button click.

  cancelGoal: (turnId: string) =>
    request<{ turn_id: string; cancelling: boolean }>(
      `/api/turn/${turnId}/cancel`,
      {
        method: "POST",
      }
    ),

  // ---- M3: course-correct a running goal -------------------------------

  /** Queue a steering message; the orchestrator drains it at the nearest
   *  decision point (between plan steps) and replans the remaining work. */
  steerGoal: (turnId: string, text: string) =>
    request<{
      turn_id: string;
      accepted: boolean;
      queued: number;
      ts?: number;
    }>(`/api/turn/${encodeURIComponent(turnId)}/steer`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  /** Authenticated URL for the Rail preview surface (GET /api/file).
   *  Media elements can't send Authorization headers — the token rides as
   *  a query param, which the bridge's auth middleware accepts for GETs
   *  precisely for this purpose. */
  fileUrl: async (path: string): Promise<string> => {
    const { url, token } = await bridgeConfig();
    const q = new URLSearchParams({ path });
    if (token) q.set("token", token);
    return `${url}/api/file?${q.toString()}`;
  },

  pendingConsents: () =>
    request<{ pending: ConsentPrompt[] }>("/api/consent/pending"),

  decideConsent: (pendingId: string, approve: boolean) =>
    request<{ pending_id: string; approved: boolean }>(
      `/api/consent/${pendingId}`,
      {
        method: "POST",
        body: JSON.stringify({ approve }),
      }
    ),

  // ---- permission modes (full / plan / ask — Phase 1, surfaced Phase 2) ----

  listCommands: () =>
    request<{
      commands: Array<{
        name: string;
        summary: string;
        usage: string;
        action: string;
        aliases: string[];
        argument_hint: string;
        allowed_tools: string[];
        supported: boolean;
        kind?: string;
        source?: string;
      }>;
    }>("/api/commands"),

  resolveCommand: (text: string) =>
    request<{
      command: null | {
        kind: string;
        name: string;
        args: string;
        prompt?: string;
        command: Record<string, unknown> | null;
      };
    }>("/api/commands/resolve", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  getPermissionMode: () =>
    request<{ mode: string; modes: string[] }>("/api/permission-mode"),

  setPermissionMode: (mode: "plan" | "edit" | "ask" | "full") =>
    request<{ mode: string }>("/api/permission-mode", {
      method: "PUT",
      body: JSON.stringify({ mode }),
    }),

  // ---- ask_user questions (structured options, consent-line sibling) ---------

  pendingQuestions: () =>
    request<{
      pending: Array<{
        pending_id: string;
        question: string;
        options: string[];
        allow_free_text: boolean;
        turn_id: string | null;
      }>;
    }>("/api/question/pending"),

  answerQuestion: (pendingId: string, answer: string) =>
    request<{ pending_id: string; answered: boolean }>(
      `/api/question/${encodeURIComponent(pendingId)}`,
      {
        method: "POST",
        body: JSON.stringify({ answer }),
      }
    ),

  // ---- message feedback (Like / Dislike on an assistant reply) ---------------

  fileFeedback: (turnId: string, rating: "like" | "dislike") =>
    request<{ ok: boolean; turn_id: string; rating: string }>("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ turn_id: turnId, rating }),
    }),

  getSettings: () =>
    request<{ settings: Settings; project_root: string; version: string }>(
      "/api/settings"
    ),

  putSettings: (patch: Partial<Settings>) =>
    request<{ settings: Settings; project_root: string; changed: string[] }>(
      "/api/settings",
      {
        method: "PUT",
        body: JSON.stringify(patch),
      }
    ),

  // ---- real LLM providers --------------------------------------------------

  getProviders: () =>
    request<{
      providers: Array<{
        name: string;
        base_url: string;
        key_count: number;
        key_aliases: string[];
        keys?: ProviderKeyInfo[];
      }>;
      currently_serving: string;
      has_provider: boolean;
      /** the (provider, model) the universe agent is pinned to — powers the
       *  composer's model chip in one poll alongside the badge */
      active_provider: string;
      active_model: string;
    }>("/api/providers"),

  getProviderCatalog: () =>
    request<{
      catalog: Array<CatalogProvider>;
      local_reachable: Record<string, boolean>;
    }>("/api/providers/catalog"),

  installProvider: (cfg: {
    name: string;
    base_url: string;
    api_key: string;
    model?: string;
    /** per-provider default model the bridge should route agents to.
     *  When omitted the bridge keeps its own sensible default — the client
     *  never forces set_default:true anymore. */
    defaultModel?: string;
    /** legacy escape hatch; prefer defaultModel. When neither is set the
     *  bridge decides (its default wins). */
    set_default?: boolean;
  }) =>
    request<{
      ok: boolean;
      provider: string;
      model: string;
      verified: boolean;
      /** false on first key / endpoint change, true when the key was
       *  APPENDED to the provider's existing pool (unlimited per provider) */
      key_appended?: boolean;
      agents_routed: string[];
      models?: string[];
    }>("/api/providers", {
      method: "POST",
      body: JSON.stringify(
        // strip the client-side alias: the wire carries model + set_default
        // only — never both defaultModel and model (bridge ignores the
        // former, and its presence confused every reader of the payload)
        (() => {
          const { defaultModel, ...rest } = cfg;
          return {
            set_default: cfg.set_default ?? false,
            ...rest,
            ...(defaultModel !== undefined ? { model: defaultModel } : {}),
          };
        })()
      ),
    }).then(r => {
      invalidateModelsCache(cfg.name);
      return r;
    }),

  /** LIVE model list of an installed provider — model choice belongs to
   *  Settings (per agent), never to a built-in list. Shared client cache +
   *  in-flight dedup: ProvidersTab auto-load, the model switcher and the
   *  AgentsTab dropdowns used to probe the same provider independently;
   *  the bridge caches its own probe for 2 minutes, so a short client TTL
   *  makes every list feel instant without going stale. */
  getProviderModels: (name: string) => providerModels(name),

  /** one API key row list for an installed provider (masked previews) */
  getProviderKeys: (name: string) =>
    request<{ provider: string; keys: ProviderKeyInfo[] }>(
      `/api/providers/${encodeURIComponent(name)}/keys`
    ),

  /** add ONE more key to an installed provider (probed live first;
   *  unlimited per provider). 422 = the key failed its live check. */
  addProviderKey: (name: string, apiKey: string) =>
    request<{ ok: boolean; provider: string; key_id: number; alias: string }>(
      `/api/providers/${encodeURIComponent(name)}/keys`,
      { method: "POST", body: JSON.stringify({ api_key: apiKey }) }
    ),

  /** remove ONE key. 409 "last_key" when it is the provider's only one. */
  removeProviderKey: (name: string, keyId: number) =>
    request<{ ok: boolean; removed: number }>(
      `/api/providers/${encodeURIComponent(name)}/keys/${keyId}`,
      { method: "DELETE" }
    ),

  /** one real request proving a voice key works before it's saved */
  checkVoiceKey: (kind: "stt" | "tts", provider: string, apiKey: string) =>
    request<{ ok: boolean; error: string | null }>("/api/voice/check", {
      method: "POST",
      body: JSON.stringify({ kind, provider, api_key: apiKey }),
    }),

  switchProviderModel: (name: string, model: string) =>
    request<{
      ok: boolean;
      provider: string;
      model: string;
      agents_routed: string[];
    }>(`/api/providers/${encodeURIComponent(name)}/model`, {
      method: "POST",
      body: JSON.stringify({ model }),
    }),

  removeProvider: (name: string) =>
    request<{ ok: boolean; removed: string }>(
      `/api/providers/${encodeURIComponent(name)}`,
      {
        method: "DELETE",
      }
    ),

  // ---- first-run onboarding gate (W3) ---------------------------------------

  getSetup: () =>
    request<{
      setup_complete: boolean;
      has_provider: boolean;
      assistant_name: string;
    }>("/api/setup"),

  completeSetup: (assistantName: string) =>
    request<{ ok: boolean; setup_complete: boolean; assistant_name: string }>(
      "/api/setup/complete",
      {
        method: "POST",
        body: JSON.stringify({ assistant_name: assistantName }),
      }
    ),

  // ---- projects + chat lifecycle (W6) ----------------------------------------

  listProjects: () =>
    request<{ projects: Array<Project & { goal_count: number }> }>(
      "/api/projects"
    ),

  createProject: (name: string, root = "") =>
    request<{ ok: boolean; project: Project }>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, root }),
    }),

  patchProject: (
    pid: string,
    patch: { name?: string; root?: string; archived?: boolean }
  ) =>
    request<{ ok: boolean; project_id: string }>(
      `/api/projects/${encodeURIComponent(pid)}`,
      {
        method: "PATCH",
        body: JSON.stringify(patch),
      }
    ),

  deleteProject: (pid: string, hard = false) =>
    request<{ ok: boolean; removed: string }>(
      `/api/projects/${encodeURIComponent(pid)}?hard=${hard}`,
      {
        method: "DELETE",
      }
    ),

  patchGoal: (
    turnId: string,
    patch: {
      title?: string;
      archived?: boolean;
      pinned?: boolean;
      project_id?: string;
    }
  ) =>
    request<{ ok: boolean; turn_id: string }>(
      `/api/turn/${encodeURIComponent(turnId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(patch),
      }
    ),

  deleteGoal: (turnId: string, hard = false) =>
    request<{ ok: boolean; deleted: string; hard: boolean }>(
      `/api/turn/${encodeURIComponent(turnId)}?hard=${hard}`,
      { method: "DELETE" }
    ),

  /** Edit-and-resend: cut the thread at one message goal — it and every
   *  later turn leave the thread (bridge deletes the rows). */
  truncateMessages: (taskId: string, messageGoalId: string) =>
    request<{ ok: boolean; removed: number; legacy?: boolean }>(
      `/api/turn/${encodeURIComponent(taskId)}/truncate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_goal_id: messageGoalId }),
      }
    ),

  branchGoal: (turnId: string) =>
    request<{
      ok: boolean;
      turn_id: string;
      session_id: string;
      text: string;
    }>(`/api/turn/${encodeURIComponent(turnId)}/branch`, { method: "POST" }),

  // ---- MCP servers (all transports) ------------------------------------------

  listMcpServers: () =>
    request<{ servers: McpServerInfo[] }>("/api/mcp/servers"),

  addMcpServer: (cfg: {
    name: string;
    transport: "stdio" | "http" | "sse";
    command?: string;
    url?: string;
    headers?: Record<string, string>;
    declared_hosts?: string[];
    read_only?: boolean;
  }) =>
    request<{ ok: boolean; server: McpServerInfo }>("/api/mcp/servers", {
      method: "POST",
      body: JSON.stringify(cfg),
    }),

  removeMcpServer: (name: string) =>
    request<{ ok: boolean; removed: string }>(
      `/api/mcp/servers/${encodeURIComponent(name)}`,
      {
        method: "DELETE",
      }
    ),

  patchMcpServer: (
    name: string,
    patch: {
      enabled?: boolean;
      transport?: "stdio" | "http" | "sse";
      command?: string;
      url?: string;
      headers?: Record<string, string>;
      declared_hosts?: string[];
      read_only?: boolean;
      verify?: boolean;
    }
  ) =>
    request<{ ok: boolean; server: McpServerInfo }>(
      `/api/mcp/servers/${encodeURIComponent(name)}`,
      {
        method: "PATCH",
        body: JSON.stringify(patch),
      }
    ),

  // ---- agents -----------------------------------------------------------------

  listAgents: () => request<{ agents: AgentInfo[] }>("/api/agents"),

  // ---- automations (Phase C — the standalone Automations page) ------------------

  listAutomations: () =>
    request<{ automations: Automation[] }>("/api/automations"),

  createAutomation: (name: string, schedule: string, prompt: string) =>
    request<{ ok: boolean; automation: Automation }>("/api/automations", {
      method: "POST",
      body: JSON.stringify({ name, schedule, prompt }),
    }),

  patchAutomation: (
    id: string,
    patch: {
      name?: string;
      schedule?: string;
      prompt?: string;
      enabled?: boolean;
    }
  ) =>
    request<{ ok: boolean; automation: Automation }>(
      `/api/automations/${encodeURIComponent(id)}`,
      { method: "PATCH", body: JSON.stringify(patch) }
    ),

  deleteAutomation: (id: string) =>
    request<{ ok: boolean; removed: string }>(
      `/api/automations/${encodeURIComponent(id)}`,
      { method: "DELETE" }
    ),

  // ---- skills (Phase C — Settings → Skills) --------------------------------------

  listSkills: (q = "", category = "") =>
    request<{
      skills: SkillInfo[];
      categories: Array<{ category: string; n: number }>;
    }>(
      `/api/skills?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}`
    ),

  getUsage: (range = "7d") =>
    request<{
      range: string;
      totalTokens: { input: number; output: number };
      calls: number;
      byProvider: Array<{
        provider: string;
        input: number;
        output: number;
        calls: number;
      }>;
      daily: Array<{ date: string; input: number; output: number }>;
    }>(`/api/usage?range=${encodeURIComponent(range)}`),
  getIndexingStatus: () =>
    request<{ enabled: boolean; root: string; count: number }>(
      `/api/indexing/status`
    ),
  resyncIndexing: () =>
    request<{ ok: boolean; indexed: number; message: string }>(
      `/api/indexing/resync`,
      { method: "POST" }
    ),
  getBrowserState: () =>
    request<{
      tabs: Array<{ id: string; title: string }>;
      activeTab: string | null;
      available: boolean;
    }>(`/api/browser/state`),
  navigateBrowser: (url: string, tab?: string) =>
    request<{ ok: boolean; result?: unknown; error?: string }>(
      `/api/browser/navigate`,
      { method: "POST", body: JSON.stringify({ url, tab }) }
    ),
  browserTabs: (args: Record<string, unknown>) =>
    request<{ ok: boolean; result?: unknown }>(`/api/browser/tabs`, {
      method: "POST",
      body: JSON.stringify(args),
    }),

  patchSkill: (id: string, enabled: boolean) =>
    request<{ ok: boolean; skill_id: string; enabled: boolean }>(
      `/api/skills/${encodeURIComponent(id)}`,
      { method: "PATCH", body: JSON.stringify({ enabled }) }
    ),

  /** the terminal rail's WS URL (token rides as a query param — browsers
   *  cannot set custom headers on WebSocket upgrades) */
  terminalSocketUrl: async (): Promise<string> => {
    const { url, token } = await bridgeConfig();
    const ws = url.replace(/^http/, "ws");
    return token
      ? `${ws}/api/terminal?token=${encodeURIComponent(token)}`
      : `${ws}/api/terminal`;
  },
};
