/**
 * components/desktop/AgentFlow.tsx — the agent message body, now the
 * shell-parity timeline (full-parity plan P1/P2). Same export signature
 * as the old Universe flow ({events, running, children}) so ChatMessages
 * integrations stay untouched; the INSIDE is Universe's:
 *
 *   reasoning rows → family-folded tool rows → SubAgent branches →
 *   compaction separators → the markdown reply → artifact chips →
 *   the trailing universe while running / the summary line when done.
 *
 * The fold is lib/flow/ztimeline.ts (Universe state machine parity),
 * rendering lives in components/flow/UniverseFlow.tsx + universe-flow.css.
 */
import { useMemo } from "react";
import { FileText, ShieldAlert } from "lucide-react";
import { useLocation } from "wouter";
import type { BridgeEvent } from "@/lib/api";
import { useChat } from "@/stores/chat";
import { useRail } from "@/lib/flow/railStore";
import { foldZTimeline } from "@/lib/flow/ztimeline";
import { ZTList } from "@/components/flow/UniverseFlow";
import "@/components/flow/universe-flow.css";

/** unique artifact files (chip order preserved from first appearance) */
function uniqueFiles(events: BridgeEvent[]): Map<string, number | null> {
  const map = new Map<string, number | null>();
  for (const ev of events) {
    if (ev.type !== "artifact_created") continue;
    if (typeof ev.path !== "string" || !ev.path) continue;
    const bytes = typeof ev.bytes === "number" && ev.bytes > 0 ? ev.bytes : null;
    map.set(ev.path, bytes);
  }
  return map;
}

export default function AgentFlow({
  events,
  running,
  children,
}: {
  events: BridgeEvent[];
  running: boolean;
  /** the markdown reply — renders between the rows and the trailing universe */
  children?: React.ReactNode;
}) {
  const consentsOpen = useChat(s => s.consents.length);
  const [, navigate] = useLocation();
  const model = useMemo(
    () => foldZTimeline(events, { consentsOpen }),
    [events, consentsOpen]
  );
  const files = useMemo(() => uniqueFiles(events), [events]);

  return (
    <div className="agent-flow">
      <ZTList model={model} running={running} />

      {model.panel.providerHalt && (
        <div className="fl-halt" role="alert">
          <ShieldAlert
            size={13}
            strokeWidth={1.8}
            style={{ color: "var(--rose)", flexShrink: 0 }}
            aria-hidden="true"
          />
          <span className="fl-halt-text">{model.panel.providerHalt}</span>
          <button
            type="button"
            className="fl-halt-btn"
            onClick={() => navigate("/settings?section=providers")}
          >
            Open settings
          </button>
        </div>
      )}

      {children}

      {files.size > 0 && (
        <div className="fl-chips">
          {Array.from(files.entries()).map(([path, bytes]) => (
            <button
              key={path}
              type="button"
              className="fl-chip"
              title={path}
              onClick={() => useRail.getState().openPreview(path)}
            >
              <FileText size={12} strokeWidth={1.8} aria-hidden="true" />
              <span className="fl-chip-name mono">
                {path.split(/[\\/]/).pop() ?? path}
              </span>
              {bytes !== null && (
                <span className="fl-chip-size mono num">
                  {bytes >= 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${bytes} B`}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* trailing universe removed per user Delete annotation */}
    </div>
  );
}
