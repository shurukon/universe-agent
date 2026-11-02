/**
 * components/desktop/Welcome.tsx — the zero-state greeting (redesign 2026-09,
 * ZCode-grade pass 2026-09-30, ZCode shell pass 2026-10).
 * Centered: the WebGL universe as the brand accent (Phase 2 item 9 — the
 * memory sphere now lives HERE, in the empty state, not scattered through
 * the working surface), a time-aware greeting, and four starter chips.
 * The no-provider alert stays (it only appears when no model is connected,
 * and without it a fresh user sends goals into silent rejection).
 */
import { lazy, Suspense, useEffect } from "react";
import { useLocation } from "wouter";
import { useChat } from "@/stores/chat";

// one WebGL renderer per page — the same shared canvas TrailingUniverse uses
const UniverseCanvas = lazy(() => import("@/components/universe-glm/UniverseCanvas"));
import { buildUniverse, type UniverseData } from "@/lib/universe/generate";

/** the same miniature orb every trailing universe shows — one build per app
 *  session, the brand accent for the empty state */
let welcomeOrb: UniverseData | null = null;
function getWelcomeOrb(): UniverseData {
  if (!welcomeOrb) welcomeOrb = buildUniverse("orb", null);
  return welcomeOrb;
}

export default function Welcome() {
  // single provider-truth mirror (chat store) — the old mount-only
  // GET /api/providers check diverged from Settings installs
  const providerServing = useChat(s => s.providerServing);
  const refreshProvider = useChat(s => s.refreshProvider);
  const hasProvider =
    providerServing === "loading" ? null : providerServing !== "none";
  const [, navigate] = useLocation();

  useEffect(() => {
    void refreshProvider();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="welcome">
      {/* the universe as the empty-state brand accent — the identity the
          trailing sphere keeps while a goal runs lives here between goals */}
      <div
        className="w-universe"
        aria-hidden="true"
        style={{
          width: 180,
          height: 180,
          marginBottom: 8,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Suspense fallback={<span className="tg-dot" />}>
          <UniverseCanvas
            data={getWelcomeOrb()}
            embedded
            width={180}
            height={180}
            interactive={false}
            autoRotate
            frozen={false}
            reducedMotion={false}
            energy={0.45}
          />
        </Suspense>
      </div>
      {/* ZCode-literal empty state (2026-10-22): the brand art alone — the
          orb stands where ZCode renders its wordmark. No greeting line, no
          eyebrow: both were Universe-only chrome the user ordered removed. */}
      {hasProvider === false && (
        <div className="w-noprovider" role="alert">
          <b>No model connected yet.</b> Universe has no built-in model — connect
          a provider first: a free-tier cloud key (Groq, Google AI Studio,
          OpenRouter…) or a local server (Ollama, LM Studio).
          <div style={{ marginTop: 12 }}>
            <button
              className="cm-btn ok"
              onClick={() => navigate("/settings")}
              aria-label="Open model provider settings"
            >
              Set up a model
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
