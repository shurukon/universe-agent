/**
 * components/desktop/ProgressPane.tsx — the Rail's fourth tab (ZCode
 * Progress pane, Phase 2 item 6). Consumes the goal's live todo_updated
 * events (Phase 1's todo.write/todo.read feed), renders the list with
 * done/in-progress/pending marks, folds not-yet-started items behind an
 * "N waiting" disclosure, and carries Jump-to-query buttons that scroll
 * the chat to each of the user's messages. The pane header shows the
 * N/M score; the Rail can collapse it all into a capsule (railStore).
 */
import { useMemo, useState } from "react";
import { Check, ChevronDown, CircleDot, LoaderCircle, PanelRightClose } from "lucide-react";
import { useChat } from "@/stores/chat";
import { useLiveGoalId } from "@/hooks/useLiveGoalId";
import { useRail } from "@/lib/flow/railStore";
import type { BridgeEvent } from "@/lib/api";

interface TodoRow {
  content: string;
  status: string;
}

export default function ProgressPane() {
  const turnId = useLiveGoalId();
  const events = useChat(s => (turnId ? s.eventsByGoal[turnId] : undefined));

  const todos = useMemo<TodoRow[]>(() => {
    if (!events) return [];
    // the LAST todo_updated in the ring is the current truth
    for (let i = events.length - 1; i >= 0; i--) {
      const ev: BridgeEvent = events[i];
      if (ev.type === "todo_updated" && Array.isArray(ev.todos)) {
        return ev.todos.map(t => ({
          content: String(t.content ?? "").slice(0, 300),
          status: String(t.status ?? "pending"),
        }));
      }
    }
    return [];
  }, [events]);

  const [showWaiting, setShowWaiting] = useState(false);

  const done = todos.filter(t => t.status === "completed").length;
  const waiting = todos.filter(t => t.status === "pending");
  const active = todos.filter(
    t => t.status !== "pending" && t.status !== "completed"
  );

  const jumpTo = (idx: number) => {
    const rows = document.querySelectorAll<HTMLElement>("#chatInner .msg-user");
    const el = rows[Math.min(idx, rows.length - 1)];
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const userCount = useChat(
    s => s.messages.filter(m => m.role === "user").length
  );

  return (
    <div className="rail-pane">
      <div className="rail-pane-head">
        <span className="rail-pane-title">Progress</span>
        {todos.length > 0 && (
          <span className="rail-pane-score mono">
            {done}/{todos.length}
          </span>
        )}
        <button
          type="button"
          className="rail-tool"
          onClick={() => useRail.getState().closeRail()}
          title="Collapse the side pane"
          aria-label="Collapse the side pane"
        >
          <PanelRightClose size={13} strokeWidth={1.8} />
        </button>
      </div>

      {todos.length === 0 && (
        <div className="prog-empty">
          No live todos yet — the assistant's todo.write updates land here in
          real time while it works.
        </div>
      )}

      {todos.length > 0 && (
        <div className="prog-list" role="list" aria-label="Task progress">
          {[...active, ...(showWaiting ? waiting : [])].map((t, i) => (
            <div
              key={`${i}-${t.content.slice(0, 40)}`}
              className={`prog-row${
                t.status === "in_progress" ? " current" : ""
              }${t.status === "completed" ? " done" : ""}`}
              role="listitem"
            >
              <span className="pr-mark" aria-hidden="true">
                {t.status === "completed" ? (
                  <Check size={12} strokeWidth={2.4} />
                ) : t.status === "in_progress" ? (
                  <span className="fl-spin">
                    <LoaderCircle size={12} strokeWidth={2.2} />
                  </span>
                ) : (
                  <CircleDot size={12} strokeWidth={1.8} />
                )}
              </span>
              <span className="pr-txt">{t.content}</span>
            </div>
          ))}
          {waiting.length > 0 && !showWaiting && (
            <button
              type="button"
              className="prog-waiting-btn"
              onClick={() => setShowWaiting(true)}
              aria-expanded={false}
            >
              <ChevronDown size={11} strokeWidth={2} />
              {waiting.length} waiting
            </button>
          )}
        </div>
      )}

      {userCount > 1 && (
        <div className="prog-jumps" aria-label="Jump to message">
          {Array.from({ length: Math.min(userCount, 5) }, (_, i) => (
            <button
              key={i}
              type="button"
              className="prog-jump"
              onClick={() => jumpTo(i)}
              title={`Jump to message ${i + 1}`}
            >
              Q{i + 1}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
