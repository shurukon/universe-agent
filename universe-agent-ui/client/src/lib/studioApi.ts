/**
 * lib/studioApi.ts — typed client for the Universe Studio surface.
 *
 * Two concerns live here:
 *   1. Tool services (open-design, AVE, Blender MCP) — list/start/stop with
 *      live status, driven by the bridge's /api/studio/services endpoints.
 *   2. Muse — the bridge's text→image endpoint (/api/meta/image), used by
 *      the Studio Muse Image card.
 *
 * Requests ride the same base URL + bearer token as lib/api.ts: the config
 * is resolved once through bridgeConfig() and every call carries
 * `Authorization: Bearer <token>` when a token is present. Server error
 * bodies ({message|error|detail}) are surfaced verbatim so toasts stay
 * honest — e.g. a missing image-provider key reads through as-is.
 */

import { bridgeConfig } from "@/lib/api";

export type StudioServiceId = "ave" | "opendesign" | "blender";

export type StudioServiceStatus =
  | "stopped"
  | "starting"
  | "running"
  | "unreachable"
  | "unconfigured";

export interface StudioService {
  id: StudioServiceId;
  name: string;
  description: string;
  url: string;
  port: number;
  status: StudioServiceStatus;
  detail?: string;
}

export interface ImageResult {
  b64?: string;
  url?: string;
}

const STATUSES: readonly StudioServiceStatus[] = [
  "stopped",
  "starting",
  "running",
  "unreachable",
  "unconfigured",
];

/** Studio errors carry the server's message (or an honest network line). */
export class StudioError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "StudioError";
    this.status = status;
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as Record<string, unknown>;
    for (const key of ["message", "error", "detail"]) {
      const value = data[key];
      if (typeof value === "string" && value.trim()) return value;
    }
  } catch {
    // body was not JSON — fall through to the generic line
  }
  return `Request failed (HTTP ${res.status})`;
}

async function studioRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const { url, token } = await bridgeConfig();
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body !== undefined) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let res: Response;
  try {
    res = await fetch(`${url}${path}`, { ...init, headers });
  } catch {
    throw new StudioError("Universe bridge is not responding", 0);
  }
  if (!res.ok) throw new StudioError(await errorMessage(res), res.status);

  // 200 with an empty body is legal for the service toggles — normalize it
  // to null so the shape guards below produce a friendly error instead of
  // a raw JSON parse exception.
  const text = await res.text();
  if (!text.trim()) return null as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    return null as T;
  }
}

// ---- shape guards (the bridge stays the source of truth; the UI stays typed)

function isService(value: unknown): value is StudioService {
  if (typeof value !== "object" || value === null) return false;
  const rec = value as Record<string, unknown>;
  return (
    typeof rec["id"] === "string" &&
    typeof rec["name"] === "string" &&
    typeof rec["description"] === "string" &&
    typeof rec["url"] === "string" &&
    typeof rec["port"] === "number" &&
    typeof rec["status"] === "string" &&
    (STATUSES as readonly string[]).includes(rec["status"])
  );
}

function asService(payload: unknown): StudioService {
  if (isService(payload)) return payload;
  if (typeof payload === "object" && payload !== null) {
    const nested = (payload as Record<string, unknown>)["service"];
    if (isService(nested)) return nested;
  }
  throw new StudioError("Unexpected response from the bridge", 0);
}

function pickImage(rec: Record<string, unknown>): ImageResult | null {
  const b64 = typeof rec["b64"] === "string" ? rec["b64"] : undefined;
  const url = typeof rec["url"] === "string" ? rec["url"] : undefined;
  if (b64 === undefined && url === undefined) return null;
  const out: ImageResult = {};
  if (b64 !== undefined) out.b64 = b64;
  if (url !== undefined) out.url = url;
  return out;
}

function asImage(payload: unknown): ImageResult {
  if (typeof payload === "object" && payload !== null) {
    const rec = payload as Record<string, unknown>;
    const direct = pickImage(rec);
    if (direct) return direct;
    if (typeof rec["image"] === "object" && rec["image"] !== null) {
      const nested = pickImage(rec["image"] as Record<string, unknown>);
      if (nested) return nested;
    }
  }
  throw new StudioError("The image provider returned nothing usable", 0);
}

// ---- endpoints -------------------------------------------------------------

/** GET /api/studio/services — every tool service with its live status. */
export async function listServices(): Promise<StudioService[]> {
  const payload = await studioRequest<unknown>("/api/studio/services");
  if (Array.isArray(payload)) {
    const services = payload.filter(isService);
    if (services.length > 0 || payload.length === 0) return services;
  } else if (typeof payload === "object" && payload !== null) {
    const nested = (payload as Record<string, unknown>)["services"];
    if (Array.isArray(nested)) return nested.filter(isService);
  }
  throw new StudioError("Unexpected response from the bridge", 0);
}

/** POST /api/studio/services/{id}/start — returns the service mid-handshake. */
export async function startService(id: StudioServiceId): Promise<StudioService> {
  return asService(
    await studioRequest<unknown>(`/api/studio/services/${id}/start`, {
      method: "POST",
    })
  );
}

/** POST /api/studio/services/{id}/stop — returns the service after shutdown. */
export async function stopService(id: StudioServiceId): Promise<StudioService> {
  return asService(
    await studioRequest<unknown>(`/api/studio/services/${id}/stop`, {
      method: "POST",
    })
  );
}

/** POST /api/meta/image — Muse text→image (webp b64 or a hosted URL). */
export async function generateImage(
  prompt: string,
  size?: string
): Promise<ImageResult> {
  const body: { prompt: string; size?: string } = { prompt };
  if (size !== undefined) body.size = size;
  return asImage(
    await studioRequest<unknown>("/api/meta/image", {
      method: "POST",
      body: JSON.stringify(body),
    })
  );
}

/* ---- AVE MCP driving (Universe edits the timeline from chat) --------------- */

export interface AveTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface AveDriveResult {
  tool: string | null;
  arguments?: Record<string, unknown>;
  result?: { isError?: boolean; data?: unknown; structured?: unknown };
  reason?: string;
  raw?: string;
}

export async function listAveTools(): Promise<{
  ok: boolean;
  reachable: boolean;
  tools: AveTool[];
  message?: string;
}> {
  return studioRequest("/api/studio/ave/tools");
}

export async function driveAve(instruction: string): Promise<AveDriveResult> {
  return studioRequest("/api/studio/ave/act", {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });
}

/* ---- SAM 3.1 segmentation (Meta Model API /v1/responses) ------------------ */

export async function segmentImage(
  prompt: string,
  imageUrl: string,
): Promise<{ output: string }> {
  return studioRequest("/api/meta/segment", {
    method: "POST",
    body: JSON.stringify({ prompt, image_url: imageUrl }),
  });
}
