/**
 * components/desktop/TransparencyBar.tsx — the ZCode-style always-on-top
 * strip: "Universe is controlling your computer" (Phase 5 structure, built
 * with the restyle; color per docs/zcode-tokens.md).
 *
 * Shows while ANY goal has a machine-control tool call in flight —
 * browser.* today, the computer-use family (machine./computer./cua.)
 * when the Phase-6 helper lands. The latest control-family lifecycle
 * event decides: requested/running → visible; finished/failed/blocked →
 * hidden. The strip overlays the top of the window — it never pushes
 * layout, exactly like ZCode's own control banner.
 */
import { TriangleAlert } from "lucide-react";
import { useChat } from "@/stores/chat";
import type { BridgeEvent } from "@/lib/api";

// ZCode tool names (Phase 2 rename): the machine-control families are the
// Browser* set + the CUA set (Screenshot/LeftClick/…) + machine./cua. hooks
const CONTROL_PREFIXES = ["Browser", "Screenshot", "LeftClick", "RightClick",
  "DoubleClick", "LongPress", "Swipe", "Scroll", "SetValue", "OpenApplication",
  "SwitchDisplay", "RequestAccess", "ListApps", "ListWindows", "GetAppState",
  "Zoom", "machine.", "cua."];
const LIFECYCLE = new Set([
  "tool_requested",
  "tool_running",
  "tool_finished",
  "tool_failed",
  "tool_blocked",
]);
const OPENERS = new Set(["tool_requested", "tool_running"]);

function isControlTool(tool: string): boolean {
  return CONTROL_PREFIXES.some(p => tool.startsWith(p));
}

/** the latest control-family lifecycle event in the thread decides the
 *  state — control calls never overlap themselves, so the newest one wins */
function latestControlIsOpen(evs: BridgeEvent[]): boolean {
  for (let i = evs.length - 1; i >= 0; i--) {
    const ev = evs[i];
    if (!LIFECYCLE.has(ev.type) || !ev.tool) continue;
    if (!isControlTool(ev.tool)) continue;
    return OPENERS.has(ev.type);
  }
  return false;
}

export default function TransparencyBar() {
  const eventsByGoal = useChat(s => s.eventsByGoal);
  // any goal with an in-flight control call keeps the bar up — the strip
  // is about the machine, not about whichever goal is being viewed
  const active = Object.values(eventsByGoal).some(latestControlIsOpen);
  if (!active) return null;
  return (
    <div className="transparency-bar" role="status" aria-live="polite">
      <TriangleAlert size={13} strokeWidth={2} aria-hidden="true" />
      Universe is controlling your computer
    </div>
  );
}
