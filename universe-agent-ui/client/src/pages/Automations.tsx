/**
 * pages/Automations.tsx — the standalone Automations page (Phase C).
 *
 * shell parity: scheduled goals on a 5-field cron, managed directly by
 * the user. The bridge (/api/automations) is a thin CRUD over the same
 * schedule.AutomationScheduler the agent's cron.* tools drive — creating
 * one here is direct user intent, so no consent gate applies; every row
 * arms/disarms with one switch and the delete is confirm-gated.
 *
 * Visual language: the Settings Linear discipline (settings-linear.css,
 * sx-* tokens) — this page adds only the am-* row styles beside it.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "wouter";
import {
  ArrowLeft,
  CalendarClock,
  Plus,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import ConfirmDialog, { ConfirmSpec } from "@/components/desktop/ConfirmDialog";
import { api, Automation, BridgeError } from "@/lib/api";

/* ---- cron → honest human text ------------------------------------------- */

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function humanizeCron(spec: string): string {
  const f = spec.trim().split(/\s+/);
  if (f.length !== 5) return spec;
  const [min, hour, dom, mon, dow] = f;
  const at = (h: string, m: string) =>
    `${h.padStart(2, "0")}:${m.padStart(2, "0")}`;
  const step = /^\/(\d+)$/.exec(min || "");
  if (step && hour === "*") return `every ${step[1]} minutes`;
  if (min !== "*" && hour === "*") return `hourly at :${min.padStart(2, "0")}`;
  if (min === "*" && hour === "*") return "every minute";
  if (min !== "*" && hour !== "*") {
    const time = at(hour, min);
    if (dow === "*" && dom === "*" && mon === "*") return `daily at ${time}`;
    if (dow !== "*" && dom === "*") {
      const days = dow.split(",").flatMap(part => {
        const [a, b] = part.split("-");
        const from = parseInt(a, 10);
        const to = b ? parseInt(b, 10) : from;
        if (Number.isNaN(from) || Number.isNaN(to)) return [];
        return Array.from(
          { length: (to - from + 8) % 7 || 7 },
          (_, i) => DOW[(from - 1 + i) % 7]
        );
      });
      return days.length ? `${days.join(", ")} at ${time}` : `${spec}`;
    }
    if (dom !== "*") return `day ${dom} of the month at ${time}`;
  }
  return spec; // honest fallback: show the raw spec, never a wrong guess
}

function relTime(ts: number | null): string {
  if (!ts) return "—";
  const d = Date.now() / 1000 - ts;
  const abs = Math.abs(d);
  const fmt = (v: number, unit: string) =>
    `${Math.round(v)}${unit}${d < 0 ? "" : " ago"}`;
  if (abs < 60) return d < 0 ? "in <1m" : "just now";
  if (abs < 3600)
    return d < 0 ? `in ${Math.round(abs / 60)}m` : fmt(abs / 60, "m");
  if (abs < 86400)
    return d < 0 ? `in ${Math.round(abs / 3600)}h` : fmt(abs / 3600, "h");
  return d < 0 ? `in ${Math.round(abs / 86400)}d` : fmt(abs / 86400, "d");
}

/* ---- the page ------------------------------------------------------------ */

export default function AutomationsPage() {
  const [rows, setRows] = useState<Automation[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null);
  // the create form
  const [name, setName] = useState("");
  const [schedule, setSchedule] = useState("");
  const [prompt, setPrompt] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.listAutomations();
      setRows(r.automations);
      setErr(null);
    } catch (e) {
      setErr(
        e instanceof BridgeError ? e.message : "Universe bridge is not responding"
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await api.createAutomation(name.trim(), schedule.trim(), prompt.trim());
      toast.success("Automation created", {
        description: `${humanizeCron(schedule)} — ${name.trim()}`,
      });
      setName("");
      setSchedule("");
      setPrompt("");
      setCreating(false);
      await load();
    } catch (e) {
      const msg =
        e instanceof BridgeError
          ? e.message
          : "Universe bridge is not responding";
      toast.error("Could not create the automation", { description: msg });
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (row: Automation, next: boolean) => {
    // optimistic flip; revert on failure — the list stays honest
    setRows(
      rs => rs?.map(r => (r.id === row.id ? { ...r, enabled: next } : r)) ?? rs
    );
    try {
      await api.patchAutomation(row.id, { enabled: next });
    } catch (e) {
      setRows(
        rs =>
          rs?.map(r => (r.id === row.id ? { ...r, enabled: !next } : r)) ?? rs
      );
      toast.error("Could not update the automation", {
        description:
          e instanceof BridgeError ? e.message : "bridge unreachable",
      });
    }
  };

  const remove = async (row: Automation) => {
    try {
      await api.deleteAutomation(row.id);
      toast("Automation deleted", { description: row.name });
      await load();
    } catch (e) {
      toast.error("Could not delete the automation", {
        description:
          e instanceof BridgeError ? e.message : "bridge unreachable",
      });
    }
  };

  return (
    <div className="app-root">
      <div className="sx-page">
        <div className="sx-shell">
          <div className="sx-content am-content">
            <div className="am-head">
              <Link
                href="/chat"
                className="sx-back"
                aria-label="Back to workspace"
              >
                <ArrowLeft size={14} strokeWidth={1.8} />
              </Link>
              <div>
                <h1 className="am-title">Automations</h1>
                <p className="am-sub">
                  Scheduled goals that run on their own — a 5-field cron
                  schedule, a prompt, and a switch.
                </p>
              </div>
            </div>

            <div className="am-toolbar">
              <span className="am-count">
                {rows === null
                  ? "…"
                  : `${rows.length} automation${rows.length === 1 ? "" : "s"}`}
              </span>
              <button
                type="button"
                className="sx-btn primary"
                onClick={() => setCreating(v => !v)}
              >
                <Plus size={13} strokeWidth={1.9} />
                New automation
              </button>
            </div>

            {creating && (
              <form
                className="sx-card am-form"
                onSubmit={e => {
                  e.preventDefault();
                  void submit();
                }}
              >
                <label className="sx-field">
                  <span className="sx-flabel">Name</span>
                  <input
                    className="sx-input"
                    value={name}
                    maxLength={120}
                    placeholder="Daily standup digest"
                    onChange={e => setName(e.target.value)}
                    autoFocus
                  />
                </label>
                <label className="sx-field">
                  <span className="sx-flabel">Schedule</span>
                  <input
                    className="sx-input mono"
                    value={schedule}
                    placeholder="0 9 * * 1-5"
                    onChange={e => setSchedule(e.target.value)}
                  />
                  <span className="sx-hint">
                    5-field cron (minute hour day month weekday) —{" "}
                    {schedule.trim() &&
                    schedule.trim().split(/\s+/).length === 5
                      ? humanizeCron(schedule)
                      : "e.g. “0 9 * * 1-5” = Mon–Fri at 09:00"}
                  </span>
                </label>
                <label className="sx-field">
                  <span className="sx-flabel">Prompt</span>
                  <textarea
                    className="sx-input am-prompt"
                    value={prompt}
                    maxLength={4000}
                    placeholder="What should run on this schedule?"
                    onChange={e => setPrompt(e.target.value)}
                  />
                </label>
                <div className="am-form-actions">
                  <button
                    type="button"
                    className="sx-btn"
                    onClick={() => setCreating(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="sx-btn primary"
                    disabled={
                      saving ||
                      !name.trim() ||
                      !schedule.trim() ||
                      !prompt.trim()
                    }
                  >
                    {saving ? "Creating…" : "Create automation"}
                  </button>
                </div>
              </form>
            )}

            {err && (
              <div className="sx-card am-empty">
                <RotateCcw size={15} strokeWidth={1.8} />
                <p>{err}</p>
                <button
                  type="button"
                  className="sx-btn"
                  onClick={() => void load()}
                >
                  Retry
                </button>
              </div>
            )}

            {rows !== null && rows.length === 0 && !creating && !err && (
              <div className="sx-card am-empty">
                <CalendarClock size={15} strokeWidth={1.8} />
                <p>
                  No automations yet — schedule a goal and it fires on its own,
                  even with the window closed behind the bridge.
                </p>
              </div>
            )}

            {rows !== null && rows.length > 0 && (
              <div className="am-list" role="list">
                {rows.map(row => (
                  <div className="am-row" role="listitem" key={row.id}>
                    <div className="am-row-main">
                      <b className="am-name">{row.name}</b>
                      <span className="am-sched mono" title={row.schedule}>
                        {humanizeCron(row.schedule)}
                      </span>
                      <span className="am-prompt-preview">{row.prompt}</span>
                    </div>
                    <div className="am-row-side">
                      <span className="am-next" title="next run">
                        {row.enabled && row.next_run_at
                          ? `next ${relTime(row.next_run_at)}`
                          : row.enabled
                            ? "next —"
                            : "off"}
                      </span>
                      <span className="am-last" title="last run">
                        {row.last_run_at
                          ? `last ${relTime(row.last_run_at)}`
                          : "never ran"}
                      </span>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={row.enabled}
                        aria-label={`${row.enabled ? "Disable" : "Enable"} ${row.name}`}
                        className={`sx-switch${row.enabled ? " on" : ""}`}
                        onClick={() => void toggle(row, !row.enabled)}
                      >
                        <span className="sx-knob" aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="am-del"
                        aria-label={`Delete ${row.name}`}
                        title="Delete automation"
                        onClick={() =>
                          setConfirm({
                            title: "Delete this automation?",
                            body: `“${row.name}” stops firing and is removed. This cannot be undone.`,
                            confirmLabel: "Delete",
                            danger: true,
                            onConfirm: () => void remove(row),
                          })
                        }
                      >
                        <Trash2 size={13} strokeWidth={1.8} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
      {confirm && (
        <ConfirmDialog spec={confirm} onClose={() => setConfirm(null)} />
      )}
    </div>
  );
}
