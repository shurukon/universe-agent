/**
 * pages/Chat.tsx — the work surface. The TopBar (App shell, Phase 2) owns
 * the sidebar toggle, window controls and back/forward; this page owns
 * the three columns: Sidebar · chat column · the right Rail. The pinned
 * StatusStrip (master-spec §A1) carries the live state line + the tiny
 * universe orb above the chat for the WHOLE run. Consent asks through the
 * inline ConsentBanner above the composer — never a modal. The right
 * Rail carries Activity · Preview · Browser · Files · Progress ·
 * Terminal; it auto-opens Activity on the first agent event of a run and
 * the Browser tab when a goal drives browser tools.
 */
import { useEffect, useRef } from "react";
import Sidebar from "@/components/desktop/Sidebar";
import ChatMessages from "@/components/desktop/ChatMessages";
import Composer from "@/components/desktop/Composer";
import ConsentBanner from "@/components/desktop/ConsentBanner";
import QuestionBanner from "@/components/desktop/QuestionBanner";
import OfflineBanner from "@/components/desktop/OfflineBanner";

import PlanPill from "@/components/desktop/PlanPill";
import Rail, { useRailBrowserActive } from "@/components/desktop/Rail";
import { useRail } from "@/lib/flow/railStore";
import { useChat } from "@/stores/chat";
import { useUi } from "@/stores/ui";
import "../components/desktop/design-system.css";
import "../components/desktop/desktop.css";
import "../components/desktop/redesign.css";
import "../components/desktop/settings-linear.css";
import "../components/desktop/flow.css";

export default function ChatPage() {
  // the sidebar's visibility is shell chrome now (Ctrl+B / TopBar), shared
  // across pages through the ui store
  const sidebarOpen = useUi(s => s.sidebarOpen);
  // the right rail lives in the shared railStore (M2): chips in the chat
  // flow and the Files pane open previews through the same state. The
  // Browser tab still auto-opens on browser-tool activity unless the user
  // closed it for this goal (dismissal sticks until the NEXT goal)
  const railOpen = useRail(s => s.open);
  const openRail = useRail(s => s.openRail);
  const closeRail = useRail(s => s.closeRail);
  const railTab = useRail(s => s.tab);
  const setRailTab = useRail(s => s.setTab);
  const dismissedRef = useRef(false);
  const tasks = useChat(s => s.tasks);
  const isEmpty = useChat(s => s.messages.length === 0);
  const isExpanded = useChat(s => s.messages.length > 0);

  const running = tasks.find(t => t.status === "running") ?? null;
  const runningGoalId = running?.turn_id ?? null;
  const browserActive = useRailBrowserActive(runningGoalId);

  // auto-open the rail's Browser tab the first time the running goal
  // drives a browser tool
  useEffect(() => {
    if (!browserActive || !runningGoalId || dismissedRef.current) return;
    openRail("browser");
  }, [browserActive, runningGoalId, openRail]);

  // auto-open the rail's Activity tab on the FIRST agent event of a run
  // (master-spec §D): the live agent tree appears by itself — unless the
  // user closed the rail for this goal (dismissal sticks until the next)
  const hasRealWork = useChat(s => {
    if (!runningGoalId) return false;
    const evs = s.eventsByGoal[runningGoalId];
    if (!evs) return false;
    return evs.some(
      e =>
        e.type === "tool_running" ||
        e.type === "tool_finished" ||
        e.type === "tool_failed"
    );
  });
  useEffect(() => {
    if (!hasRealWork || dismissedRef.current) return;
    openRail("activity");
  }, [hasRealWork, openRail]);

  // dismissal resets per goal — a new goal may open it again
  useEffect(() => {
    dismissedRef.current = false;
  }, [runningGoalId]);

  // finished run: the auto-opened Activity pane returns to the picker —
  // the live Universe window shows "Open tab" on a finished task (measured
  // 2026-10-23). Runs end → picker; viewing another finished task → picker.
  // (A tab the user opens inside a finished task stays put: the reset only
  // fires on a goal change or on the run ending.)
  const viewedId = useChat(s => s.viewedGoalId ?? s.activeTaskId);
  const viewedRunning = tasks.some(
    t => t.turn_id === viewedId && t.status === "running"
  );
  const prevRunningRef = useRef<string | null>(null);
  useEffect(() => {
    if (prevRunningRef.current && !runningGoalId && railTab === "activity") {
      setRailTab("open");
    }
    prevRunningRef.current = runningGoalId;
  }, [runningGoalId, railTab, setRailTab]);

  const lastViewedRef = useRef<string | null>(null);
  useEffect(() => {
    const changed = lastViewedRef.current !== viewedId;
    if (changed && !viewedRunning && railTab === "activity") {
      setRailTab("open");
    }
    lastViewedRef.current = viewedId;
  }, [viewedId, viewedRunning, railTab, setRailTab]);

  return (
    <div className="app-root">
      <OfflineBanner />
      <div className={`app${sidebarOpen ? "" : " sb-closed"}`}>
        <Sidebar open={sidebarOpen} />
        <main
          className={`main${isEmpty ? " empty" : ""}${isExpanded ? " main--expanded" : ""}`}
        >
          {/* StatusStrip removed per user annotation — top "Working / Agents 1 running" strip deleted */}
          <PlanPill />
          <ChatMessages />
          <ConsentBanner />
          <QuestionBanner />
          {/* Universe order: the chat flow, banners, then the composer — the
              composer card now carries its own workspace head row (the
              project crumbs moved inside the card), and the stripped-under
              card bar + hint line are gone (Universe 3.12.1 home has none) */}
          <Composer centered={isEmpty} />
        </main>
        {railOpen && (
          <Rail
            browserActive={browserActive}
            onClose={() => {
              dismissedRef.current = true;
              closeRail();
            }}
          />
        )}
        {/* the floating edge-toggle is retired (Universe: the panel
            opens from the view header's side-panel icon on the top bar) */}
      </div>
    </div>
  );
}
