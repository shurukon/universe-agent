/**
 * components/desktop/WorkspaceSwitcher.tsx — the Universe | Studio | Bots
 * (+ Nebula) segmented control. Lives at the TOP OF THE LEFT SIDEBAR (user
 * decision, 2026-11): the sidebar is the stable shell — it only changes
 * shape in Bots mode (the bots rail takes the same column). TopBar no
 * longer carries it. Styled on the Universe-measured neutral tokens — no
 * blue-tinted glass.
 */
import { Sparkles } from "lucide-react";
import { useLocation } from "wouter";

export default function WorkspaceSwitcher() {
  const [location, navigate] = useLocation();
  return (
    <nav className="zsb-ws-toggle" aria-label="Workspace switcher">
      <button
        type="button"
        data-active={location === "/chat" || location === "/"}
        onClick={() => navigate("/chat")}
      >
        Universe
      </button>
      <button
        type="button"
        data-active={location.startsWith("/studio")}
        onClick={() => navigate("/studio")}
      >
        Studio
      </button>
      <button
        type="button"
        data-active={location.startsWith("/bots")}
        onClick={() => navigate("/bots")}
      >
        Bots
      </button>
      <button
        type="button"
        data-active={location.startsWith("/nebula")}
        onClick={() => navigate("/nebula")}
        title="Nebula — memory"
        aria-label="Nebula memory"
      >
        <Sparkles size={12} strokeWidth={1.8} />
      </button>
    </nav>
  );
}
