/**
 * components/desktop/ChatMessages.tsx — the conversation column (M1).
 * user bubble · agent card whose whole body is the AgentFlow: brain row +
 * per-agent tool rows + collapsed agents + the markdown reply + the
 * trailing universe as the LAST live element. No MiniUniverse, no
 * ActivityFeed cards, no ThinkingSpark — all deleted.
 *
 * ZCode rows (Phase 2): every message carries a timestamp; user rows get
 * Edit (branch-and-resend) + Copy; assistant rows get Copy, Like/Dislike
 * (POST /api/feedback), Fork (branch), Hooks (the intercepts popover);
 * the assistant header shows the measured Working for…/Worked for… badge.
 */
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useSearch, useLocation } from "wouter";
import { toast } from "sonner";
import {
  Check,
  ChevronRight,
  Copy,
  GitFork,
  PencilLine,
  ThumbsDown,
  ThumbsUp,
  Anchor,
} from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { api } from "@/lib/api";
import { copyToClipboard } from "@/lib/clipboard";
import { useChat } from "@/stores/chat";
import { useUi } from "@/stores/ui";
import Welcome from "./Welcome";
import AgentFlow from "./AgentFlow";
import AgentReply from "./AgentReply";

function fmtClock(ts?: number): string | null {
  if (!ts) return null;
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function ChatMessages() {
  const messages = useChat(s => s.messages);
  const viewedGoalId = useChat(s => s.viewedGoalId);
  const scrollRef = useRef<HTMLDivElement>(null);
  const search = useSearch();

  useEffect(() => {
    // the scroller is .chat-scroll (overflow-y:auto) — .chat-inner is the
    // static column inside it and scrolling it is a no-op
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // WS events append to per-goal rings without touching `messages`; the
  // column follows them through a store SUBSCRIPTION, not a subscription
  // to the whole eventsByGoal record — every event used to re-render this
  // whole component (and, unmemoized, every row with it). Subscribing
  // keeps the autoscroll glued to the trailing universe while the component
  // itself re-renders only when the message list identity changes.
  useEffect(() => {
    let last = useChat.getState().eventsByGoal;
    return useChat.subscribe(state => {
      if (state.eventsByGoal === last) return;
      last = state.eventsByGoal;
      const el = scrollRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }, []);

  // previous-conversation view: /?goal=<id> loads that goal's log (history
  // + filter to it). Runs on mount / goal change only — never on refresh.
  useEffect(() => {
    const turnId = new URLSearchParams(search).get("goal");
    if (!turnId) return;
    const store = useChat.getState();
    if (store.viewedGoalId === turnId) return;
    if (store.messages.some(m => m.turn_id === turnId)) {
      // already in the live conversation — just pin the filter, no refetch
      useChat.setState({ viewedGoalId: turnId });
      return;
    }
    void store.loadGoalHistory(turnId);
  }, [search]);

  // screen-reader narration of the latest event per running goal — the
  // trailing universe is visual; this is its text equivalent
  const narration = useChat(s => {
    const running = s.tasks.filter(t => t.status === "running");
    if (!running.length) return "";
    const lines = running.map(t => {
      const evs = s.eventsByGoal[t.turn_id] ?? [];
      const last = evs[evs.length - 1];
      return last ? `${t.text}: ${last.type.replace(/_/g, " ")}` : "";
    });
    return lines.filter(Boolean).join(". ");
  });

  if (messages.length === 0) return <Welcome />;

  // Filtering by turn_id isolates one legacy goal's bubbles in a shared live
  // conversation. Threaded tasks differ: loadGoalHistory replaces `messages`
  // with the whole thread, whose bubbles carry per-message goal ids (never the
  // task id), so the filter matches nothing and the past view goes blank. When
  // a task view yields no match, the loaded thread IS the conversation — show it.
  let visible = viewedGoalId
    ? messages.filter(m => m.turn_id === viewedGoalId)
    : messages;
  if (viewedGoalId && visible.length === 0) visible = messages;

  return (
    <div className="chat-scroll" id="chatScroll" ref={scrollRef}>
      {/* aria-live: the visible universe impression, as text for AT */}
      <span className="sr-only" role="status" aria-live="polite">
        {narration}
      </span>
      {/* No "back to live" banner: in the threaded model an opened task IS
          the live conversation — sending continues inside it. */}
      <div className="chat-inner" id="chatInner">
        {visible.map(m =>
          m.role === "user" ? (
            <UserRow key={m.id} text={m.text} turnId={m.turn_id} ts={m.ts} />
          ) : (
            <AgentMessage
              key={m.id}
              turnId={m.turn_id}
              streaming={!!m.streaming}
              text={m.text}
            />
          )
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* user row — the ZCode SINGLE bubble (the old glass card around it was
   the second bubble the user flagged 2026-10-30). Hover actions: Copy +
   Edit (seed the composer — the threaded model continues the session) +
   the timestamp, exactly the measured reference crop.                    */

const UserRow = memo(function UserRow({
  text,
  turnId,
  ts,
}: {
  text: string;
  turnId: string;
  ts?: number;
}) {
  const [copied, setCopied] = useState(false);
  const clock = fmtClock(ts);

  // Edit = ZCode branch-and-resend mapped onto this app's threaded model:
  // the message text returns to the composer AND the message turn plus
  // every later turn leave the thread (bridge truncate)
  const onEdit = async () => {
    const res = await useChat.getState().truncateThread(turnId);
    if (res === "cut") {
      useUi.getState().seedComposer(text);
      toast.success("Back in the composer — the later messages were removed");
    } else if (res === "legacy") {
      useUi.getState().seedComposer(text);
      toast("Old chats edit by resending — the original stays in history");
    } else {
      toast.error(
        "Could not remove the messages yet (a turn is still running?)"
      );
    }
  };

  const onCopy = async () => {
    if (await copyToClipboard(text)) {
      setCopied(true);
    } else {
      toast.error("Copy failed — clipboard unavailable");
    }
  };

  return (
    <div className="msg-user">
      <div className="m-bubble" dir="auto">
        {text}
      </div>
      <div className="m-actions">
        {clock && <span className="m-ts mono">{clock}</span>}
        <button
          type="button"
          className="m-act"
          aria-label="Copy message"
          title="Copy message"
          onClick={() => void onCopy()}
        >
          {copied ? (
            <Check size={14} strokeWidth={1.8} aria-hidden="true" />
          ) : (
            <Copy size={14} strokeWidth={1.8} aria-hidden="true" />
          )}
        </button>
        <button
          type="button"
          className="m-act"
          aria-label="Edit message"
          title="Edit and resend"
          onClick={() => void onEdit()}
        >
          <PencilLine size={14} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
});

// one shared empty array: a fresh [] per snapshot would re-render forever
const NO_EVENTS: never[] = [];

// the agent row is memoized the same way — turnId/streaming/text are
// primitives, so only the goal whose ring actually changed re-renders
// (its own store subscriptions fire inside the row)
const AgentMessage = memo(function AgentMessage({
  turnId,
  streaming,
  text,
}: {
  turnId: string;
  streaming: boolean;
  text: string;
}) {
  const [copied, setCopied] = useState(false);
  const [rating, setRating] = useState<"like" | "dislike" | null>(null);
  const [hooksOpen, setHooksOpen] = useState(false);
  const events = useChat(s => s.eventsByGoal[turnId] ?? NO_EVENTS);
  // stable selectors: zustand v5 compares with Object.is, so find() must not
  // run inline (a fresh undefined/array per call re-renders forever)
  // The task row lives under the TASK container id (task-…), not the
  // message turn id (turn-…): the wire's turn_started.task_id publishes
  // that mapping (taskByMessage) — resolve it or `running` stays false for
  // the whole live turn and the Work panel never opens (live fix 2026-11-01)
  const ownerTaskId = useChat(
    s => (s.taskByMessage as Record<string, string> | undefined)?.[turnId] ?? null
  );
  const task = useChat(
    useShallow(
      s => s.tasks.find(t => t.turn_id === (ownerTaskId ?? turnId)) ?? null
    )
  );
  const running = task?.status === "running";
  const [, navigate] = useLocation();
  const refreshTasks = useChat(s => s.refreshTasks);
  // Work panel: open while the agent is running, collapsed after it finishes.
  // Matches the "why is it folded" annotation — running turn exposes timeline.
  const [histOpen, setHistOpen] = useState(!!running);
  useEffect(() => {
    setHistOpen(!!running);
  }, [running, turnId]);

  // Working for…/Worked for… — measured from turn_started to the terminal
  // event (or to now while running), ticking once a second while live
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!running) return;
    const iv = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(iv);
  }, [running]);
  const span = useMemo(() => {
    const start =
      events.find(e => e.type === "turn_started" || e.type === "turn_started")
        ?.ts ??
      (events[0] as unknown as { ts?: number })?.ts ??
      null;
    if (start == null) return null;
    const end =
      events.find(
        e =>
          e.type === "turn_completed" ||
          e.type === "turn_failed" ||
          e.type === "turn_cancelled" ||
          e.type === "turn_completed" ||
          e.type === "turn_failed" ||
          e.type === "turn_cancelled"
      )?.ts ??
      (running
        ? now
        : ((events[events.length - 1] as unknown as { ts?: number })?.ts ??
          null));
    if (end == null) return null;
    const s = Math.max(0, end - start);
    if (s < 1 && events.length < 2) return null;
    if (s < 1) return "a few seconds";
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    return h > 0 ? `${h}h ${m}m ${sec}s` : m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  }, [events, running, now]);

  const hooks = useMemo(
    () => events.filter(e => e.type === "hook_intercepted"),
    [events]
  );

  const copy = async () => {
    const ok = await copyToClipboard(text);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } else {
      toast.error("Copy failed — clipboard unavailable");
    }
  };

  const rate = async (r: "like" | "dislike") => {
    // idempotent in the UI: clicking the same button again does nothing
    if (rating === r) return;
    setRating(r);
    try {
      await api.fileFeedback(turnId, r);
    } catch {
      toast.error("Feedback could not be saved");
      setRating(null);
    }
  };

  const fork = async () => {
    try {
      const r = await api.branchGoal(turnId);
      await refreshTasks();
      navigate(`/chat?goal=${encodeURIComponent(r.turn_id)}`);
      toast.success("Forked into a new chat");
    } catch {
      toast.error("Could not fork this reply");
    }
  };

  return (
    <div className="msg-agent">
      {/* ZCode: Work header sits ABOVE answer, collapsible */}
      {span && (
        <div className="zturn-foot">
          <button
            type="button"
            className="zhist"
            aria-expanded={histOpen}
            onClick={() => setHistOpen(v => !v)}
          >
            <ChevronRight
              size={16}
              strokeWidth={1.5}
              className="ztr-chev"
              style={{
                transform: histOpen ? "rotate(90deg)" : undefined,
                transition: "transform 150ms ease",
              }}
              aria-hidden="true"
            />
            <span>
              {running ? "Working for" : "Worked for"} {span}
            </span>
          </button>
          {histOpen && (
            <div className="zhist-panel">
              <AgentFlow events={events} running={running} />
            </div>
          )}
        </div>
      )}

      {/* When no Work header (edge case), still show timeline standalone */}
      {!span && events.length > 0 && (
        <AgentFlow events={events} running={running} />
      )}

      <AgentReply text={text} streaming={streaming} />

      {!running && text && (
        <div className="zacts">
          <button
            className="m-act2"
            onClick={() => void fork()}
            aria-label="Fork from here"
            title="Fork — copy the conversation from this point"
          >
            <GitFork size={14} strokeWidth={1.5} />
          </button>
          <button
            className="m-act2"
            onClick={copy}
            aria-label="Copy reply"
            title="Copy reply"
          >
            {copied ? (
              <Check size={14} strokeWidth={1.5} />
            ) : (
              <Copy size={14} strokeWidth={1.5} />
            )}
          </button>
          <button
            className={`m-act2${rating === "like" ? " on" : ""}`}
            onClick={() => void rate("like")}
            aria-label="Good reply"
            title="Like — saved to the feedback table"
            aria-pressed={rating === "like"}
          >
            <ThumbsUp size={14} strokeWidth={1.5} />
          </button>
          <button
            className={`m-act2${rating === "dislike" ? " on" : ""}`}
            onClick={() => void rate("dislike")}
            aria-label="Bad reply"
            title="Dislike — saved to the feedback table"
            aria-pressed={rating === "dislike"}
          >
            <ThumbsDown size={14} strokeWidth={1.5} />
          </button>
          {hooks.length > 0 && (
            <span className="m-hooks-wrap">
              <button
                className="m-act2"
                onClick={() => setHooksOpen(v => !v)}
                aria-label="Hooks that intercepted this goal"
                title="Hooks"
                aria-expanded={hooksOpen}
              >
                <Anchor size={14} strokeWidth={1.5} />
              </button>
              {hooksOpen && (
                <div
                  className="m-hooks-card"
                  role="dialog"
                  aria-label="Hook intercepts"
                >
                  <b>hooks</b>
                  {hooks.map((h, i) => (
                    <div key={i} className="hk-row mono">
                      {h.hook_id ?? "hook"} · {h.action ?? "block"} ·{" "}
                      {h.tool ?? ""}
                      {h.message ? ` — ${h.message.slice(0, 160)}` : ""}
                    </div>
                  ))}
                </div>
              )}
            </span>
          )}
          {task?.status === "completed" && (
            <span className="zturn-state">done</span>
          )}
          {task?.status === "failed" && (
            <span className="zturn-state">failed</span>
          )}
          {task?.status === "cancelled" && (
            <span className="zturn-state">stopped</span>
          )}
        </div>
      )}
    </div>
  );
});
