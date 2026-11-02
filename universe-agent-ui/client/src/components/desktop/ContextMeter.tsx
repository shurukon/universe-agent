import { useMemo } from "react";
import { useChat } from "@/stores/chat";
import { foldZTimeline } from "@/lib/flow/ztimeline";

export default function ContextMeter() {
  const turnId = useChat(s => s.viewedGoalId ?? s.activeTaskId);
  const events = useChat(s => (turnId ? s.eventsByGoal[turnId] : undefined));
  const goalText = useChat(s => {
    if (!turnId) return null;
    const t = s.tasks.find(x => x.turn_id === turnId);
    return t?.text ?? null;
  });
  const messagesLen = useChat(s => s.messages.length);
  const activeModel = useChat(s => s.activeModel);
  const usage = useMemo(() => {
    if (!events) return null;
    const m = foldZTimeline(events, { taskText: goalText });
    return m.panel.contextUsage ?? null;
  }, [events, goalText]);

  if (messagesLen === 0) return null;
  const used = usage?.used ?? 0;
  let total = usage?.total ?? 0;
  const estimate = usage?.estimate ?? true;
  if (!total) {
    const l = (activeModel || "").toLowerCase();
    if (l.includes("claude")) total = 200_000;
    else if (l.includes("gemini")) total = 1_000_000;
    else if (l.includes("gpt-4") || l.includes("nemotron")) total = 128_000;
    else if (l.includes("deepseek")) total = 64_000;
    else total = 128_000;
  }
  const pct = total > 0 ? Math.min(100, Math.max(0, (used / total) * 100)) : 0;
  const r = 6;
  const circ = 2 * Math.PI * r;
  const dash = (pct / 100) * circ;
  const color =
    pct > 80 ? "#ef4444" : pct > 60 ? "#f59e0b" : "rgba(255,255,255,0.35)";

  return (
    <span
      className="ctx-meter"
      title={`Context: ${used.toLocaleString()} / ${total.toLocaleString()}${estimate ? " (est.)" : ""} — ${pct.toFixed(1)}%`}
    >
      <svg
        width={14}
        height={14}
        viewBox="0 0 14 14"
        className="ctx-meter-ring"
        aria-hidden="true"
      >
        <circle
          cx={7}
          cy={7}
          r={r}
          fill="none"
          stroke="rgba(255,255,255,0.12)"
          strokeWidth={1.4}
        />
        {pct > 0.5 && (
          <circle
            cx={7}
            cy={7}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={1.4}
            strokeLinecap="round"
            strokeDasharray={`${dash} ${circ - dash}`}
            transform="rotate(-90 7 7)"
          />
        )}
      </svg>
    </span>
  );
}
