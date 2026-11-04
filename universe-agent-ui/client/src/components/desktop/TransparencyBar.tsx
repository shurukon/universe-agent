/**
 * components/desktop/TransparencyBar.tsx — the ZCode-style always-on-top
 * strip: "Universe is controlling your computer" (Phase 5 structure, built
 * with the restyle; restyled to the black/faint-white control pill).
 *
 * Shows while ANY RUNNING goal has used a machine-control tool call —
 * browser.* today, the computer-use family (machine./computer./cua.) when
 * the Phase-6 helper lands. Once a control tool appears the strip STAYS up
 * for the whole run (the agent may think, switch apps, open windows between
 * control calls — the user must always know the machine is driven), and it
 * drops the moment the run reaches a terminal state. The strip floats over
 * the top of the interface — it never pushes layout, exactly like ZCode's
 * own control banner — and it mirrors the live chat column width so it
 * reads as part of the chat surface in every layout.
 */
import { useEffect, useState } from "react";
import { Monitor } from "lucide-react";
import { useChat } from "@/stores/chat";

// ZCode tool names (Phase 2 rename): the machine-control families are the
// Browser* set + the CUA set (Screenshot/LeftClick/…) + machine./cua. hooks
const CONTROL_PREFIXES = ["Browser", "Screenshot", "LeftClick", "RightClick",
  "DoubleClick", "LongPress", "Swipe", "Scroll", "SetValue", "OpenApplication",
  "SwitchDisplay", "RequestAccess", "ListApps", "ListWindows", "GetAppState",
  "Zoom", "machine.", "cua."];

function isControlTool(tool: string): boolean {
  return CONTROL_PREFIXES.some(p => tool.startsWith(p));
}

export default function TransparencyBar() {
  const eventsByGoal = useChat(s => s.eventsByGoal);
  const tasks = useChat(s => s.tasks);
  const taskByMessage = useChat(
    s => s.taskByMessage as Record<string, string> | undefined
  );

  // a goal qualifies while its RUN is still going: the owner task (the wire
  // stamps turn_started.task_id → taskByMessage) must be running AND the
  // goal must have driven a control-family tool at least once this run
  const runningTaskIds = new Set(
    tasks.filter(t => t.status === "running").map(t => t.turn_id)
  );
  const active = Object.entries(eventsByGoal).some(([gid, evs]) => {
    const owner = taskByMessage?.[gid] ?? gid;
    if (!runningTaskIds.has(owner)) return false;
    return evs.some(e => !!e.tool && isControlTool(e.tool));
  });

  // mirror the composer's live box: same horizontal span as the chat
  // column, whatever the rail/sidebar layout is doing
  const [box, setBox] = useState<{ left: number; width: number } | null>(null);
  useEffect(() => {
    if (!active) return;
    const measure = () => {
      const el = document.querySelector<HTMLElement>(".composer");
      if (!el) {
        setBox(null);
        return;
      }
      const r = el.getBoundingClientRect();
      setBox({ left: r.left, width: r.width });
    };
    measure();
    window.addEventListener("resize", measure);
    const iv = window.setInterval(measure, 800);
    return () => {
      window.removeEventListener("resize", measure);
      window.clearInterval(iv);
    };
  }, [active]);

  if (!active) return null;
  return (
    <div
      className="transparency-bar"
      role="status"
      aria-live="polite"
      style={box ? { left: box.left, width: box.width, transform: "none" } : undefined}
    >
      <span className="tb-dot" aria-hidden="true" />
      <Monitor size={13} strokeWidth={1.8} aria-hidden="true" />
      Universe is controlling your computer
    </div>
  );
}
