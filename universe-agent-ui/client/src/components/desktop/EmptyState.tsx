/**
 * components/desktop/EmptyState.tsx — the Universe home's zero-state:
 * the WebGL universe orb as the brand accent, plus the honest no-provider
 * alert (a fresh user must not send goals into silent rejection).
 * There is no onboarding page and no gate — the composer works as soon as
 * a provider is connected.
 */
import { lazy, Suspense, useEffect } from "react";
import { useChat } from "@/stores/chat";

// one WebGL renderer per page — the same shared canvas TrailingUniverse uses
const UniverseCanvas = lazy(
  () => import("@/components/universe-sphere/UniverseCanvas")
);
import { buildUniverse, type UniverseData } from "@/lib/universe/generate";

/** the same miniature orb every trailing universe shows — one build per
 *  app session, the brand accent for the empty state */
let emptyOrb: UniverseData | null = null;
function getEmptyOrb(): UniverseData {
  if (!emptyOrb) emptyOrb = buildUniverse("orb", null);
  return emptyOrb;
}

export default function EmptyState() {
  const providerServing = useChat(s => s.providerServing);
  const refreshProvider = useChat(s => s.refreshProvider);
  const hasProvider =
    providerServing === "loading" ? null : providerServing !== "none";

  useEffect(() => {
    void refreshProvider();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="welcome">
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
            data={getEmptyOrb()}
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
      {hasProvider === false && (
        <div className="w-noprovider" role="alert">
          <p>
            <b>No model connected yet.</b> Universe has no built-in model —
            connect a provider first: a free-tier cloud key (Groq, Google AI
            Studio, OpenRouter...) or a local server (Ollama, LM Studio).
          </p>
          <a href="/settings">
            <button type="button" className="w-noprovider-btn">
              Set up a model
            </button>
          </a>
        </div>
      )}
    </div>
  );
}
