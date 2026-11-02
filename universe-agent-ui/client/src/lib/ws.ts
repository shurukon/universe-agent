/**
 * lib/ws.ts — the live event stream client.
 *
 * Connects to WS /api/events and feeds every event into the zustand store.
 * Reconnect discipline (plan §7.3): exponential backoff 500ms → 10s, 3
 * attempts; after that the store flips to `offline` (banner) — but keeps
 * trying slowly in the background; when it comes back, per-goal missed
 * events are refilled from GET /api/turn/{id}/events so the constellation
 * has no holes.
 *
 * Heartbeats (15s) are consumed silently; they double as liveness proof.
 */
import { api, BridgeEvent, bridgeConfig } from "./api";

export type WsState = "connecting" | "live" | "reconnecting" | "offline";

type Handlers = {
  onEvent: (ev: BridgeEvent) => void;
  onState: (s: WsState) => void;
  onHello?: (info: { version: string }) => void;
  /** bridge settings changed elsewhere — re-fetch consents + settings */
  onSettingsUpdated?: () => void;
};

const HEARTBEAT_TIMEOUT_MS = 25_000; // > 15s bridge heartbeat; 2 missed = dead
const BASE_DELAY = 500;
const MAX_DELAY = 10_000;
const FAST_ATTEMPTS = 3;

export class UniverseWs {
  private ws: WebSocket | null = null;
  private handlers: Handlers;
  private state: WsState = "connecting";
  private attempts = 0;
  private closedByUser = false;
  private lastEventTs = 0;
  private livenessTimer: ReturnType<typeof setInterval> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(handlers: Handlers) {
    this.handlers = handlers;
  }

  get current(): WsState {
    return this.state;
  }

  private setState(s: WsState) {
    if (this.state === s) return;
    this.state = s;
    this.handlers.onState(s);
  }

  connect() {
    this.closedByUser = false;
    this.setState(this.attempts === 0 ? "connecting" : "reconnecting");
    void this.openSocket();
  }

  private async openSocket(): Promise<void> {
    // WS auth: browsers cannot set custom headers on WebSocket — the token
    // rides as a query param (the bridge's auth middleware accepts either)
    const { url: baseUrl, token } = await bridgeConfig();
    const url =
      baseUrl.replace(/^http/, "ws") +
      `/api/events${token ? `?token=${encodeURIComponent(token)}` : ""}`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.attempts = 0;
      this.lastEventTs = Date.now();
      this.setState("live");
      this.watchLiveness();
    };

    ws.onmessage = msg => {
      this.lastEventTs = Date.now();
      let ev: BridgeEvent;
      try {
        ev = JSON.parse(msg.data as string) as BridgeEvent;
      } catch {
        return;
      }
      if (ev.type === "heartbeat") return;
      if (ev.type === "bridge_hello") {
        this.handlers.onHello?.({
          version: ev.version ?? "",
        });
        return;
      }
      if (ev.type === "settings_updated") {
        this.handlers.onSettingsUpdated?.();
        return;
      }
      this.handlers.onEvent(ev);
    };

    ws.onclose = () => {
      this.stopLiveness();
      if (this.closedByUser) return;
      this.scheduleReconnect();
    };

    ws.onerror = () => {
      // onclose follows; let it drive the reconnect
    };
  }

  private watchLiveness() {
    this.stopLiveness();
    this.livenessTimer = setInterval(() => {
      if (Date.now() - this.lastEventTs > HEARTBEAT_TIMEOUT_MS) {
        // 2+ missed heartbeats: treat the socket as dead even if open
        this.ws?.close();
      }
    }, 5_000);
  }

  private stopLiveness() {
    if (this.livenessTimer) clearInterval(this.livenessTimer);
    this.livenessTimer = null;
  }

  private scheduleReconnect() {
    this.attempts += 1;
    const fast = this.attempts <= FAST_ATTEMPTS;
    const delay = Math.min(BASE_DELAY * 2 ** (this.attempts - 1), MAX_DELAY);
    this.setState(fast ? "reconnecting" : "offline");
    this.retryTimer = setTimeout(
      () => this.connect(),
      fast ? delay : MAX_DELAY
    );
  }

  /** Full reconnect + refill of everything missed while offline. */
  async resync(goalIds: string[], lastSeqByGoal: Record<string, number>) {
    for (const turnId of goalIds) {
      const after = lastSeqByGoal[turnId] ?? 0;
      try {
        const refill = await api.refill(turnId, after);
        for (const ev of refill.events) this.handlers.onEvent(ev);
      } catch {
        // goal unknown to the bridge session (e.g. bridge restarted): the
        // goal detail fetch is the caller's fallback
      }
    }
  }

  close() {
    this.closedByUser = true;
    this.stopLiveness();
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.ws?.close();
    this.ws = null;
  }
}
