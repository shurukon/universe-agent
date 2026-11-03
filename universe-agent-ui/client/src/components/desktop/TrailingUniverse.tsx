/**
 * components/desktop/TrailingUniverse.tsx — THE thinking loader of Universe
 * Computer (M1). One small transparent universe (~34px, no background, no
 * box) that lives as the LAST element of the running agent's message —
 * under the latest tool row, under the streaming reply, always trailing.
 *
 * · shape + rotation never change; only the base color shifts with the
 *   working agent (random session tints until the backend phase fixes
 *   the table) and returns to the resting brand color between agents
 * · spin speed follows live event energy (tools/handoffs = flurries)
 * · fades out 600ms the moment the goal ends — replaced by the summary
 *   line, never a frozen frame
 * · prefers-reduced-motion: static dot + the text line only
 */
import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { buildUniverse, type UniverseData } from "@/lib/universe/generate";
import { useUniverse } from "@/components/universe-sphere/state";
import { agentTintDeg, tintFilter } from "@/lib/flow/agentTint";
import type { BridgeEvent } from "@/lib/api";
import type { FlowStatus } from "@/lib/flow/fold";

// single WebGL renderer per page — shared with the universe-sphere lazy chunk
const UniverseCanvas = lazy(() =>
  import("@/components/universe-sphere/UniverseCanvas").then(m => ({
    default: m.default,
  }))
);

const SIZE = 34;

/** Build the orb dataset once per app session (static seed — every trailing
 *  universe in every message is the same miniature sphere). Exported so the
 *  pinned StatusStrip orb shares the SAME dataset — one build per session. */
let orbCache: UniverseData | null = null;
export function getOrbData(): UniverseData {
  if (!orbCache) orbCache = buildUniverse("orb", null);
  return orbCache;
}

export default function TrailingUniverse({
  events,
  running,
  agent,
  status,
}: {
  events: BridgeEvent[];
  running: boolean;
  agent: string | null;
  status: FlowStatus;
}) {
  const [reducedMotion] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
  const setMode = useUniverse(s => s.setMode);
  // fade-out window: the goal ended — hold 600ms, then vanish for good
  const [fading, setFading] = useState(false);
  const [gone, setGone] = useState(false);

  useEffect(() => {
    if (running) {
      setFading(false);
      setGone(false);
      return;
    }
    setFading(true);
    const t = setTimeout(() => setGone(true), 600);
    return () => clearTimeout(t);
  }, [running]);

  // drive the shared mode store: 'reasoning' lights the core + orbit
  // shaders (CosmicCore MODE_TARGETS). Scoped per mount — unmount
  // restores idle so a finished goal never leaks glow elsewhere.
  useEffect(() => {
    if (reducedMotion || !running) return;
    setMode("reasoning");
    return () => setMode("idle");
  }, [running, reducedMotion, setMode]);

  // live agent-activity energy (0..1): events per second over a sliding
  // ~2s window, eased — the universe visibly thinks harder during bursts.
  const energy = useMemo(() => {
    if (!running || events.length < 2) return 0;
    const now = Date.now() / 1000;
    let n = 0;
    for (let i = events.length - 1; i >= 0; i--) {
      if (now - events[i].ts > 2) break;
      n++;
    }
    return Math.min(n / 8, 1);
  }, [events, running]);

  if (gone) return null;

  const tint = agentTintDeg(agent);
  const isError = status.kind === "error";
  const label = status.text;

  return (
    <div
      className={`trailing-universe${fading ? " fade" : ""}`}
      role="status"
      aria-live="polite"
    >
      <span className="tg-canvas" aria-hidden="true">
        {reducedMotion ? (
          <span className="tg-dot" />
        ) : (
          <Suspense fallback={<span className="tg-dot" />}>
            <span
              className="tg-tint"
              style={{
                filter: tintFilter(tint),
                transition: "filter .3s ease",
              }}
            >
              <UniverseCanvas
                data={getOrbData()}
                embedded
                width={SIZE}
                height={SIZE}
                interactive={false}
                autoRotate
                frozen={false}
                reducedMotion={false}
                energy={energy}
              />
            </span>
          </Suspense>
        )}
      </span>
      <span className={`tg-status mono${isError ? " err" : ""}`}>{label}</span>
    </div>
  );
}
