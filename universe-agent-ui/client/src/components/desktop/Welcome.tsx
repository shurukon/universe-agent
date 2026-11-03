/**
 * components/desktop/Welcome.tsx — the zero-state greeting (redesign 2026-09,
 * ZCode-grade pass 2026-09-30, ZCode shell pass 2026-10, Manus-cards pass
 * 2026-11). The WebGL universe stays the brand accent; below it the
 * Manus-2.0-Lite-style start cards — Build / Create stacked on the left,
 * the local-folder card on the right — all on the measured neutral tokens.
 * The no-provider alert stays (it only appears when no model is connected,
 * and without it a fresh user sends goals into silent rejection).
 */
import { lazy, Suspense, useEffect } from "react";
import { useLocation } from "wouter";
import {
  Braces,
  ChevronRight,
  Folder,
  Hammer,
  Layers,
  Sparkles,
} from "lucide-react";
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

  // Build → hand the stage to the composer (the agent builds it)
  const focusComposer = () => {
    const el = document.querySelector<HTMLTextAreaElement>(
      ".composer textarea"
    );
    el?.focus();
  };
  // Start from a work folder → the sidebar's add-project flow (native
  // folder picker in the desktop app; honest toast in the dev browser)
  const startLocal = () => {
    window.dispatchEvent(new CustomEvent("universe:add-project"));
  };

  return (
    <div className="welcome">
      {/* the universe as the empty-state brand accent — the identity the
          trailing sphere keeps while a goal runs lives here between goals */}
      <div
        className="w-universe"
        aria-hidden="true"
        style={{
          width: 150,
          height: 150,
          marginBottom: 4,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Suspense fallback={<span className="tg-dot" />}>
          <UniverseCanvas
            data={getWelcomeOrb()}
            embedded
            width={150}
            height={150}
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
          <b>No model connected yet.</b> Universe has no built-in model — connect
          a provider first: a free-tier cloud key (Groq, Google AI Studio,
          OpenRouter…) or a local server (Ollama, LM Studio).
          <div style={{ marginTop: 10 }}>
            <button
              className="w-noprovider-btn"
              onClick={() => navigate("/settings")}
              aria-label="Open model provider settings"
            >
              Set up a model
            </button>
          </div>
        </div>
      )}

      {/* Manus-style start cards (reference: Manus 2.0 Lite home) — Build /
          Create stacked left, the local-folder card right, all in the
          Universe neutral card language */}
      <div className="w-cards" role="group" aria-label="Start points">
        <div className="w-cards-col">
          <button type="button" className="w-card" onClick={focusComposer}>
            <span className="w-card-icon w-ci-build" aria-hidden="true">
              <Hammer size={16} strokeWidth={1.9} />
            </span>
            <span className="w-card-body">
              <span className="w-card-title">
                Build
                <ChevronRight size={14} strokeWidth={2} aria-hidden="true" />
              </span>
              <span className="w-card-sub">Websites, apps, and games</span>
            </span>
            <span className="w-card-art" aria-hidden="true">
              <Braces size={110} strokeWidth={1} />
            </span>
          </button>
          <button
            type="button"
            className="w-card"
            onClick={() => navigate("/studio")}
          >
            <span className="w-card-icon w-ci-create" aria-hidden="true">
              <Sparkles size={16} strokeWidth={1.9} />
            </span>
            <span className="w-card-body">
              <span className="w-card-title">
                Create
                <ChevronRight size={14} strokeWidth={2} aria-hidden="true" />
              </span>
              <span className="w-card-sub">Slides, images, and videos</span>
            </span>
            <span className="w-card-art" aria-hidden="true">
              <Layers size={110} strokeWidth={1} />
            </span>
          </button>
        </div>
        <button type="button" className="w-card w-card-tall" onClick={startLocal}>
          <span className="w-card-eyebrow" aria-hidden="true">
            Desktop
          </span>
          <span className="w-card-art-big" aria-hidden="true">
            <Folder size={26} strokeWidth={1.5} />
          </span>
          <span className="w-card-title-c">Start from a work folder</span>
          <span className="w-card-sub-c">Open a local folder as a project</span>
        </button>
      </div>
    </div>
  );
}
