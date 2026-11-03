/**
 * lib/botsApi.ts — typed client for the Bots surface (/api/bots*).
 *
 * A thin sibling of lib/api.ts: same bridgeConfig() base URL, same
 * `Authorization: Bearer <token>` discipline, same BridgeError
 * normalization ({code, message} + status) — scoped to the bot
 * CRUD / run / skill / schedule / approval endpoints.
 *
 * Polling (detail every 4s) is owned by the page, not here.
 */
import { bridgeConfig, BridgeError } from "@/lib/api";

/* ---- wire shapes (mirror the bridge contract exactly) ------------------- */

export type BotStatus = "idle" | "running" | "waiting_approval" | "error" | "paused";
export type ScheduleKind = "none" | "daily" | "interval" | "cron";
export type ApprovalDecision = "allow_once" | "always_allow" | "deny";

export interface BotSchedule {
  kind: ScheduleKind;
  /** daily only — "HH:MM" */
  time?: string;
  /** cron expr, or interval length like "30m" */
  expr?: string;
  /** IANA name, e.g. "Europe/Rome" */
  tz?: string;
}

export interface Bot {
  id: string;
  name: string;
  description: string;
  status: BotStatus;
  schedule: BotSchedule;
  /** the skill this bot currently runs with, if any */
  skill: string | null;
  skills: string[];
  created_at: string;
  last_run_at: string | null;
  last_result: string | null;
  approvals_pending: number;
  workspace: string;
}

export interface BotEvent {
  ts: string;
  role: "user" | "bot" | "system" | "approval";
  text: string;
}

export interface BotDetail extends Bot {
  transcript: BotEvent[];
  /** the bot's live/last Universe turn — the SAME event bus the chat
   *  streams, so the SAME timeline renders the bot's run */
  last_turn_id?: string | null;
  /** the bot's task container in the goals table (owner 'bot:<id>') */
  container_id?: string | null;
}

export interface BotApproval {
  id: string;
  ts: string;
  kind: string;
  summary: string;
  detail: string;
}

/* ---- request plumbing ---------------------------------------------------- */

async function botsRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const { url, token } = await bridgeConfig();
  let res: Response;
  try {
    res = await fetch(`${url}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new BridgeError("unreachable", "Universe bridge is not responding", 0);
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
  const text = await res.text();
  if (!text) return {} as T; // empty-body mutations (204 / "{}")
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new BridgeError(
      "bad_response",
      "bridge returned a malformed response",
      res.status
    );
  }
}

const seg = (id: string) => encodeURIComponent(id);

/* ---- endpoints ------------------------------------------------------------ */

export async function listBots(): Promise<Bot[]> {
  // bridge envelope: {"bots": [...]}
  const r = await botsRequest<{ bots?: Bot[] }>("/api/bots");
  return Array.isArray(r?.bots) ? r.bots : [];
}

export async function createBot(
  name: string,
  description: string
): Promise<Bot> {
  // bridge envelope: {"bot": {...}}
  const r = await botsRequest<{ bot?: Bot }>("/api/bots", {
    method: "POST",
    body: JSON.stringify({ name, description }),
  });
  if (!r?.bot) throw new BridgeError("bad_response", "bridge returned no bot", 0);
  return r.bot;
}

export async function getBot(id: string): Promise<BotDetail> {
  return botsRequest<BotDetail>(`/api/bots/${seg(id)}`);
}

export async function runBot(
  id: string,
  task: string
): Promise<{ run_id: string }> {
  return botsRequest<{ run_id: string }>(`/api/bots/${seg(id)}/run`, {
    method: "POST",
    body: JSON.stringify({ task }),
  });
}

export async function stopBot(id: string): Promise<void> {
  await botsRequest<Record<string, never>>(`/api/bots/${seg(id)}/stop`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function setBotSkill(
  id: string,
  name: string
): Promise<{ skill: string }> {
  return botsRequest<{ skill: string }>(`/api/bots/${seg(id)}/skill`, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function setBotSchedule(
  id: string,
  schedule: BotSchedule
): Promise<Bot> {
  // bridge envelope: {"bot": {...}}
  const r = await botsRequest<{ bot?: Bot }>(`/api/bots/${seg(id)}/schedule`, {
    method: "POST",
    body: JSON.stringify(schedule),
  });
  if (!r?.bot) throw new BridgeError("bad_response", "bridge returned no bot", 0);
  return r.bot;
}

export async function clearBotSchedule(id: string): Promise<Bot> {
  // bridge envelope: {"bot": {...}}
  const r = await botsRequest<{ bot?: Bot }>(`/api/bots/${seg(id)}/schedule/clear`, {
    method: "POST",
  });
  if (!r?.bot) throw new BridgeError("bad_response", "bridge returned no bot", 0);
  return r.bot;
}

export async function listApprovals(id: string): Promise<BotApproval[]> {
  // bridge envelope: {"approvals": [...]}
  const r = await botsRequest<{ approvals?: BotApproval[] }>(
    `/api/bots/${seg(id)}/approvals`
  );
  return Array.isArray(r?.approvals) ? r.approvals : [];
}

export async function decideApproval(
  id: string,
  approvalId: string,
  decision: ApprovalDecision
): Promise<void> {
  await botsRequest<Record<string, never>>(
    `/api/bots/${seg(id)}/approvals/${seg(approvalId)}`,
    { method: "POST", body: JSON.stringify({ decision }) }
  );
}

export async function deleteBot(id: string): Promise<void> {
  await botsRequest<Record<string, never>>(`/api/bots/${seg(id)}`, {
    method: "DELETE",
  });
}

/** Create-via-chat: describe the bot in one line; the configured engine
 *  drafts the name + job definition (engine=false → the raw text rode
 *  through because no provider is reachable). */
export async function draftBot(
  text: string
): Promise<{ name: string; description: string; engine: boolean }> {
  return botsRequest<{ name: string; description: string; engine: boolean }>(
    "/api/bots/draft",
    { method: "POST", body: JSON.stringify({ text }) }
  );
}

export interface ToolInfo {
  name: string;
  description: string;
  capability: string;
  consent: boolean;
}

/** The REAL Universe tool inventory — the same registry every turn
 *  resolves through. Bots carry literally these tools. */
export async function listTools(): Promise<ToolInfo[]> {
  const r = await botsRequest<{ tools?: ToolInfo[]; count?: number }>(
    "/api/tools"
  );
  return Array.isArray(r?.tools) ? r.tools : [];
}

export interface SharedFile {
  name: string;
  size: number;
  mtime: string | null;
}

/** The bots' shared computer — one directory every bot reads/writes. */
export async function sharedFiles(): Promise<{
  path: string;
  files: SharedFile[];
}> {
  return botsRequest<{ path: string; files: SharedFile[] }>(
    "/api/bots/shared/files"
  );
}

/** One-shot mic transcription (Meta · muse-voice-transcribe-1.0 — the
 *  voice model is selected automatically, there is exactly one). The
 *  browser records webm/opus; the bridge converts to 16k mono WAV. */
export async function transcribeAudio(blob: Blob): Promise<string> {
  const { url, token } = await bridgeConfig();
  const form = new FormData();
  form.append("audio", blob, "clip.webm");
  let res: Response;
  try {
    res = await fetch(`${url}/api/meta/transcribe`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
  } catch {
    throw new BridgeError("unreachable", "Universe bridge is not responding", 0);
  }
  if (!res.ok) {
    let message = `transcription failed (${res.status})`;
    try {
      const body = (await res.json()) as {
        error?: { message?: string };
        detail?: string;
      };
      message = body?.error?.message ?? body?.detail ?? message;
    } catch {
      /* keep the default */
    }
    throw new BridgeError("stt_failed", message, res.status);
  }
  const r = (await res.json()) as { text?: string };
  return (r?.text ?? "").trim();
}

/**
 * The screen stream is loaded in an <iframe>, which cannot carry an
 * Authorization header — the token rides along as a query param instead
 * (omitted entirely when the bridge runs tokenless in dev browser mode).
 */
export async function botScreenUrl(id: string): Promise<string> {
  const { url, token } = await bridgeConfig();
  const base = `${url}/api/bots/${seg(id)}/screen`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}
