/**
 * components/desktop/ConsentBanner.tsx — the M1 consent prompt. Replaces
 * the old ConsentModal: an inline banner docked directly ABOVE the
 * composer (it pushes the composer down) — no scrim, no blocking, the
 * conversation stays readable behind it. Approve / Deny · NO countdown:
 * the prompt waits for the human decision for as long as it takes (the
 * bridge no longer auto-denies), so there is no timer to outlive — the
 * banner simply stays until the user answers. Escape denies.
 */
import { useEffect, useRef } from "react";
import { ShieldAlert } from "lucide-react";
import { useChat } from "@/stores/chat";

export default function ConsentBanner() {
  const consents = useChat(s => s.consents);
  const decide = useChat(s => s.decideConsent);

  const prompt = consents[0];

  // Escape denies (the only paths out are the two explicit buttons or
  // this decline — no hidden bypass, no timeout).
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

  if (!prompt) return null;

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
