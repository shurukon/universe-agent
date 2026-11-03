/**
 * components/desktop/TopBar.tsx — ZCode-literal chrome (2026-10-23 pass).
 *
 * The LIVE 3.12.1 window measured today shows ONE strip across the window:
 *   [sidebar toggle][back][forward] ......... [side-panel toggle][min][max][close]
 * The session title + its "…" menu live INSIDE the chat column's task
 * header (TaskHeader.tsx) — not in the window strip.
 */
import { Minus, PanelRight, X } from "lucide-react";
import { useLocation } from "wouter";
import { useUi } from "@/stores/ui";
import { useRail } from "@/lib/flow/railStore";
import { ArrowLeftIcon, ArrowRightIcon, PanelLeftIcon, UniverseMark } from "./zcode-icons";

type UniverseWin = {
  win?: (action: "minimize" | "maximize" | "close") => Promise<boolean>;
};

function preload(): UniverseWin | null {
  const g = (window as unknown as { universe?: UniverseWin }).universe;
  return g?.win ? g : null; // non-Electron dev browser: no window buttons
}

export default function TopBar() {
  const [, navigate] = useLocation();
  const toggleSidebar = useUi(s => s.toggleSidebar);
  const sidebarOpen = useUi(s => s.sidebarOpen);
  const canBack = useUi(s => s.canBack());
  const canForward = useUi(s => s.canForward());
  const navBack = useUi(s => s.navBack);
  const navForward = useUi(s => s.navForward);
  const railOpen = useRail(s => s.open);
  const openRail = useRail(s => s.openRail);
  const closeRail = useRail(s => s.closeRail);

  const win = preload();

  const goHistory = (dir: -1 | 1) => {
    const target = dir < 0 ? navBack() : navForward();
    if (target) navigate(target);
  };

  return (
    <div className="topbar-rows" role="banner" aria-label="Application chrome">
      {/* two-tone strip: the leading 264px rides the sidebar column (panel
          tone); with the sidebar collapsed the whole strip falls back to
          canvas so no darker block floats over the chat column */}
      <header className={`zt-strip${sidebarOpen ? "" : " sb-off"}`}>
        {/* ZCode's window cluster: the app tile doubles as the sidebar
            toggle (hover swaps the mark for a panel glyph), then the two
            history arrows at 50% while disabled. Update pill omitted —
            Universe has no updater to report. */}
        <span className="zsb-strip-lead">
          <button
            type="button"
            className="zsb-tb zsb-tb-logo"
            onClick={() => toggleSidebar()}
            title={
              sidebarOpen
                ? "Collapse sidebar (Ctrl+B)"
                : "Open sidebar (Ctrl+B)"
            }
            aria-label="Toggle sidebar"
            aria-pressed={sidebarOpen}
          >
            <UniverseMark size={20} />
            <span className="zsb-tb-logo-alt" aria-hidden="true">
              <PanelLeftIcon size={16} />
            </span>
          </button>
          <button
            type="button"
            className="zsb-tb"
            disabled={!canBack}
            onClick={() => goHistory(-1)}
            title="Back"
            aria-label="Back"
          >
            <ArrowLeftIcon size={16} />
          </button>
          <button
            type="button"
            className="zsb-tb"
            disabled={!canForward}
            onClick={() => goHistory(1)}
            title="Forward"
            aria-label="Forward"
          >
            <ArrowRightIcon size={16} />
          </button>
        </span>

        {/* The workspace toggle (Universe | Studio | Bots | Nebula) lives at
            the TOP OF THE LEFT SIDEBAR now (WorkspaceSwitcher) — the sidebar
            is the stable shell and only changes shape in Bots mode. */}
        <span className="zt-strip-flex" aria-hidden="true" />

        <button
          type="button"
          className="tb-btn"
          onClick={() => (railOpen ? closeRail() : openRail())}
          title={railOpen ? "Collapse side panel" : "Expand side panel"}
          aria-label="Toggle side panel"
          aria-pressed={railOpen}
        >
          <PanelRight size={14} strokeWidth={1.8} />
        </button>

        {win && (
          <span className="tb-win" role="group" aria-label="Window controls">
            <button
              type="button"
              onClick={() => void win.win!("minimize")}
              aria-label="Minimize"
              title="Minimize"
            >
              <Minus size={13} strokeWidth={2} />
            </button>
            <button
              type="button"
              onClick={() => void win.win!("maximize")}
              aria-label="Maximize or restore"
              title="Maximize / restore"
            >
              <svg width="11" height="11" viewBox="0 0 11 11" aria-hidden="true">
                <rect
                  x="0.5"
                  y="0.5"
                  width="10"
                  height="10"
                  fill="none"
                  stroke="currentColor"
                />
              </svg>
            </button>
            <button
              type="button"
              className="tb-close"
              onClick={() => void win.win!("close")}
              aria-label="Close"
              title="Close"
            >
              <X size={13} strokeWidth={2} />
            </button>
          </span>
        )}
      </header>
    </div>
  );
}
