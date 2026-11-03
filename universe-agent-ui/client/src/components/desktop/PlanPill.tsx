/**
 * components/desktop/PlanPill.tsx — the floating phase chip.
 *
 * Measured on the live Universe 3.12.1 window (2026-10-23): while a goal runs,
 * a small pill floats over the top of the timeline — "› Phase 6 — full
 * pytest green + tsc/eslint/buil…" — carrying the CURRENT plan step. The
 * data comes from the same fold the rail's Plans section uses (the live
 * TodoWrite list — turn_planned legacy steps still honored), so nothing
 * here is invented: no plan, no pill.
 */
import { ChevronRight } from "lucide-react";
import { useMemo } from "react";
import { useChat } from "@/stores/chat";
import { useRail } from "@/lib/flow/railStore";
import { foldZTimeline } from "@/lib/flow/ztimeline";

export default function PlanPill() {
  const turnId = useChat(s => s.viewedGoalId ?? s.activeTaskId);
  const events = useChat(s => (turnId ? s.eventsByGoal[turnId] : undefined));
  const goalText = useChat(s => {
    const t = turnId ? s.tasks.find(x => x.turn_id === turnId) : undefined;
    return t?.title ?? null;
  });
  const openRail = useRail(s => s.openRail);

  const model = useMemo(
    () => (events ? foldZTimeline(events, { taskText: goalText }) : null),
    [events, goalText]
  );

  if (!model || model.panel.plans.length === 0) return null;
  const running = model.panel.running;
  const plans = model.panel.plans;
  // running → the todo cursor picks the phase; finished → the last phase
  // of the plan doc (Universe keeps the pill on a finished task, measured)
  const idx = running
    ? Math.min(Math.max(model.panel.todo?.idx ?? 0, 0), plans.length - 1)
    : plans.length - 1;
  const step = plans[idx];

  return (
    <button
      type="button"
      className={`plan-pill${running ? " at-strip" : ""}`}
      title="Open the plan in the side panel"
      onClick={() => openRail("activity")}
    >
      <ChevronRight size={12} strokeWidth={2.2} aria-hidden="true" />
      <span className="pp-label">
        Phase {step.step + 1} — {step.instruction}
      </span>
    </button>
  );
}
