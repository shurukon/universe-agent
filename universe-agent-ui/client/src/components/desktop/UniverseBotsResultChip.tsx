/**
 * components/desktop/UniverseBotsResultChip.tsx — the chip a delegation
 * into universe bots leaves behind.
 *
 * The work went to a bot in the other app; its result comes back later,
 * as an event. This row is that result: who did it, how it ended, what
 * came out, and one button that takes the conversation over into the
 * bots surface.
 */
import { useLocation } from "wouter";
import { Bot, CheckCircle2, XCircle, AlertTriangle, ArrowUpRight } from "lucide-react";

export interface BotsResult {
  assignmentId: string;
  botName: string;
  status: string;
  outcome: string;
  result: string;
  text: string;
}

function tone(status: string): { icon: typeof CheckCircle2; className: string } {
  if (status === "completed") return { icon: CheckCircle2, className: "ubrc-ok" };
  if (status === "failed") return { icon: XCircle, className: "ubrc-bad" };
  if (status === "cancelled") return { icon: AlertTriangle, className: "ubrc-warn" };
  return { icon: AlertTriangle, className: "ubrc-warn" };
}

export default function UniverseBotsResultChip({ result }: { result: BotsResult }) {
  const [, navigate] = useLocation();
  const { icon: Icon, className } = tone(result.status);
  const detail = (result.result || result.text || "").trim();
  return (
    <div className="ubrc" data-testid="universe-bots-result">
      <span className={`ubrc-icon ${className}`}>
        <Icon size={14} aria-hidden="true" />
      </span>
      <span className="ubrc-name">
        <Bot size={13} aria-hidden="true" />
        {result.botName || "Universe Bots"}
      </span>
      <span className="ubrc-text" title={detail}>
        {detail || result.status}
      </span>
      <button
        type="button"
        className="ubrc-take"
        title="Open the bots surface and take the conversation over there"
        onClick={() => navigate("/bots")}
      >
        Take over
        <ArrowUpRight size={13} aria-hidden="true" />
      </button>
    </div>
  );
}
