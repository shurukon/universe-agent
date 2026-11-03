/**
 * lib/nebulaApi.ts — typed HTTP client for the Nebula memory vault
 * (/api/nebula/*).
 *
 * Mirrors the transport discipline of lib/api.ts: base URL + auth token are
 * resolved through bridgeConfig() (Electron preload → env → dev default),
 * every call carries `Authorization: Bearer <token>` when a token exists,
 * and failures are normalized to BridgeError so pages catch exactly one
 * error shape. Mutation verbs are never retried (a dropped connection after
 * send could otherwise duplicate a distill or a delete).
 */
import { bridgeConfig, BridgeError } from "@/lib/api";

// ---- types -----------------------------------------------------------------

export interface NebulaNoteMeta {
  id: string;
  title: string;
  tags: string[];
  links: number;
  backlinks: number;
  updated_at: string;
}

export interface NebulaNote extends NebulaNoteMeta {
  content: string;
  links_to: string[];
  backlinks_from: string[];
}

export interface NebulaGraphNode {
  id: string;
  title: string;
  degree: number;
}

export interface NebulaGraphEdge {
  src: string;
  dst: string;
}

export interface NebulaGraph {
  nodes: NebulaGraphNode[];
  edges: NebulaGraphEdge[];
}

export interface NebulaDistillResult {
  created: string[];
}

// ---- transport -------------------------------------------------------------

const REQUEST_TIMEOUT_MS = 30_000;

/** One attempt, one normalized error — the caller decides on toasts. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const { url, token } = await bridgeConfig();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${url}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
      signal: ctrl.signal,
    });
  } catch {
    throw new BridgeError(
      "unreachable",
      "Universe bridge is not responding",
      0
    );
  } finally {
    clearTimeout(timer);
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
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new BridgeError(
      "bad_response",
      "Nebula bridge returned invalid JSON",
      res.status
    );
  }
}

/** Tolerant list unwrap: accepts a bare array or a {notes|items|data} envelope. */
function asList<T>(body: unknown, key: string): T[] {
  if (Array.isArray(body)) return body as T[];
  if (body && typeof body === "object") {
    const rec = body as Record<string, unknown>;
    for (const k of [key, "items", "data"] as const) {
      const v = rec[k];
      if (Array.isArray(v)) return v as T[];
    }
  }
  return [];
}

/** Tolerant object unwrap: accepts the object itself or a {key: …} envelope. */
function asObj<T>(body: unknown, key: string): T {
  if (body && typeof body === "object" && key in (body as Record<string, unknown>)) {
    const inner = (body as Record<string, unknown>)[key];
    if (inner && typeof inner === "object") return inner as T;
  }
  return body as T;
}

// ---- endpoints ---------------------------------------------------------------

export async function listNotes(): Promise<NebulaNoteMeta[]> {
  return asList<NebulaNoteMeta>(
    await request<unknown>("/api/nebula/notes"),
    "notes"
  );
}

export async function getNote(id: string): Promise<NebulaNote> {
  return asObj<NebulaNote>(
    await request<unknown>(`/api/nebula/notes/${encodeURIComponent(id)}`),
    "note"
  );
}

export async function createNote(
  title: string,
  content: string
): Promise<NebulaNoteMeta> {
  return asObj<NebulaNoteMeta>(
    await request<unknown>("/api/nebula/notes", {
      method: "POST",
      body: JSON.stringify({ title, content }),
    }),
    "note"
  );
}

export async function updateNote(
  id: string,
  content: string
): Promise<NebulaNoteMeta> {
  return asObj<NebulaNoteMeta>(
    await request<unknown>(`/api/nebula/notes/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
    "note"
  );
}

export async function deleteNote(id: string): Promise<void> {
  await request<void>(`/api/nebula/notes/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function getGraph(): Promise<NebulaGraph> {
  const body = asObj<NebulaGraph>(
    await request<unknown>("/api/nebula/graph"),
    "graph"
  );
  return {
    nodes: Array.isArray(body?.nodes) ? body.nodes : [],
    edges: Array.isArray(body?.edges) ? body.edges : [],
  };
}

export async function distill(sessionId?: string): Promise<NebulaDistillResult> {
  const body = asObj<NebulaDistillResult>(
    await request<unknown>("/api/nebula/distill", {
      method: "POST",
      body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
    }),
    "result"
  );
  return { created: Array.isArray(body?.created) ? body.created : [] };
}
