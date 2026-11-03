/**
 * components/desktop/ConsentBanner.tsx — the M1 consent prompt. Replaces
 * the old ConsentModal: an inline banner docked directly ABOVE the
 * composer (it pushes the composer down) — no scrim, no blocking, the
 * conversation stays readable behind it. Approve / Deny · honest 60s
 * auto-deny countdown (the bridge enforces it; this visualizes it).
 */
import { useEffect, useRef, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { useChat } from "@/stores/chat";

/** bridge-side auto-deny window (seconds) when the prompt carries none */
const FALLBACK_TIMEOUT_S = 60;

/** seconds left until the bridge auto-denies: deadline = created_at + timeout
 *  (mounted late — e.g. after a refresh — still shows the honest remainder) */
function secondsLeft(
  prompt: { created_at: number } | undefined,
  timeoutS: number
): number {
  if (!prompt || !prompt.created_at) return timeoutS;
  // created_at rides the wire in seconds (time.time()); tolerate ms too
  const createdS =
    prompt.created_at > 1e12 ? prompt.created_at / 1000 : prompt.created_at;
  return Math.max(0, Math.ceil(createdS + timeoutS - Date.now() / 1000));
}

export default function ConsentBanner() {
  const consents = useChat(s => s.consents);
  const decide = useChat(s => s.decideConsent);
  const [left, setLeft] = useState(FALLBACK_TIMEOUT_S);

  const prompt = consents[0];
  const timeoutS =
    (prompt as { timeout_s?: number } | undefined)?.timeout_s ??
    FALLBACK_TIMEOUT_S;

  useEffect(() => {
    if (!prompt) return;
    setLeft(secondsLeft(prompt, timeoutS));
    const iv = setInterval(() => setLeft(secondsLeft(prompt, timeoutS)), 1000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prompt?.pending_id]);

  // Escape denies (matches the auto-deny default; no hidden bypass — the
  // only paths out are the two explicit buttons, timeout, or this decline).
  // capture + stopPropagation: the composer's Esc-to-interrupt must not
  // fire while a consent owns the keyboard.
  const decideRef = useRef(decide);
  decideRef.current = decide;
  const promptId = prompt?.pending_id;
  useEffect(() => {
    if (!promptId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        void decideRef.current(promptId, false);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
    // pending_id identity is the subscription — the prompt object itself
    // changes on every store snapshot and would re-bind the listener
  }, [promptId]);

  // the bridge auto-denies at 0s — the banner must follow suit (decide
  // clears it locally first; a 404 from an already-resolved prompt is the
  // happy path, not an error)
  useEffect(() => {
    if (!promptId || left > 0) return;
    const t = setTimeout(() => void decideRef.current(promptId, false), 1200);
    return () => clearTimeout(t);
  }, [promptId, left]);

  if (!prompt) return null;

  const pct = timeoutS > 0 ? left / timeoutS : 0;

  return (
    <div className="consent-banner" role="alert">
      <div className="cb-glyph" aria-hidden="true">
        <ShieldAlert size={15} strokeWidth={1.8} />
      </div>
      <div className="cb-body">
        <div className="cb-title">
          Needs your approval — <b className="mono">{prompt.tool}</b> from{" "}
          <b>{prompt.agent || "agent"}</b>
        </div>
        {prompt.args_summary && (
          <div className="cb-args mono">{prompt.args_summary}</div>
        )}
        <div className="cb-track" aria-hidden="true">
          <div
            className="cb-fill"
            style={{ width: `${Math.max(0, pct) * 100}%` }}
          />
        </div>
      </div>
      <div
        className="cb-timer mono num"
        aria-label={`${left} seconds until automatic deny`}
      >
        {left}s
      </div>
      <div className="cb-actions">
        <button
          type="button"
          className="cb-btn deny"
          onClick={() => void decide(prompt.pending_id, false)}
        >
          Deny
        </button>
        <button
          type="button"
          className="cb-btn ok"
          autoFocus
          onClick={() => void decide(prompt.pending_id, true)}
        >
          Approve
        </button>
      </div>
    </div>
  );
}
