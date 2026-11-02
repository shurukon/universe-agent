/**
 * components/desktop/StatusCard.tsx — the right rail's top status stack.
 *
 * Section order (each section renders ONLY when it has data — an absent
 * row is cleaner than a fake one):
 *
 *   Git tools  Changes (from the goal's file-edit stream) · Branch
 *              (real: parsed from .git/HEAD via the file API) · a
 *              Commit-or-push trigger that says where git actions live
 *              in this build.
 *   Goal       the task's goal text + a live elapsed chip.
 *   Progress   the live todo list: done score in the header, completed
 *              items folded behind "N completed", pending behind
 *              "N waiting".
 *   Agents · Workflows · Terminals   live counters from the same panel.
 *
 * Data source: the ztimeline panel of the pane goal (viewed goal, else
 * the active task's live message) — the exact model the chat status
 * strip renders, so the two surfaces can never disagree.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDot,
  FilePen,
  Flag,
  ListChecks,
  List,
  LoaderCircle,
  SquareTerminal,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";
import { useChat } from "@/stores/chat";
import { useLiveGoalId } from "@/hooks/useLiveGoalId";
import { api } from "@/lib/api";
import {
  foldZTimeline,
  fmtShortDuration,
  type ZTModel,
  type ZTTodoItem,
} from "@/lib/flow/ztimeline";

/* ------------------------------------------------------------------ */
/* state — same pane-goal selection as ProgressPane                    */
/* ------------------------------------------------------------------ */

function usePaneGoal(): {
  turnId: string | null;
  model: ZTModel | null;
  running: boolean;
  goalText: string | null;
} {
  const turnId = useLiveGoalId();
  const events = useChat(s => (turnId ? s.eventsByGoal[turnId] : undefined));
  const streaming = useChat(s =>
    turnId ? s.messages.some(m => m.turn_id === turnId && m.streaming) : false
  );
  // the Goal section's text: the store's task row (the synth/demo rings carry
  // no task_text event, so the fold alone would leave the section empty)
  const goalText = useChat(s =>
    turnId ? (s.tasks.find(t => t.turn_id === turnId)?.text ?? null) : null
  );
  const model = useMemo(
    () => (events ? foldZTimeline(events, { taskText: goalText }) : null),
    [events, goalText]
  );
  return { turnId, model, running: streaming, goalText };
}

/* tick for the Goal elapsed chip */
function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [running]);
  return now;
}

/* the workspace branch — parsed straight from .git/HEAD (with worktree fallback) */
function useBranch(): string | null {
  const [branch, setBranch] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const headUrl = await api.fileUrl(".git/HEAD");
        const res = await fetch(headUrl);
        let txt = "";
        if (res.ok) txt = (await res.text()).trim();
        // handle worktree where .git is a file containing gitdir:
        if (!txt || txt.startsWith("gitdir:")) {
          try {
            const gitFileUrl = await api.fileUrl(".git");
            const gitFileRes = await fetch(gitFileUrl);
            if (gitFileRes.ok) {
              const gitFileTxt = (await gitFileRes.text()).trim();
              const mDir = gitFileTxt.match(/^gitdir:\s*(.+)$/);
              if (mDir) {
                const commondirUrl = await api.fileUrl(
                  mDir[1].replace(/\.git.*$/, ".git/HEAD")
                );
                const cRes = await fetch(commondirUrl);
                if (cRes.ok) txt = (await cRes.text()).trim();
              }
            }
          } catch {
            /* ignore worktree fallback */
          }
        }
        if (!txt) return;
        const m = txt.match(/^ref:\s*refs\/heads\/(.+)$/);
        if (m && !cancelled) setBranch(m[1]);
        else if (/^[0-9a-f]{40}$/i.test(txt) && !cancelled)
          setBranch(txt.slice(0, 7));
        else if (txt && !cancelled) setBranch(txt.slice(0, 24));
      } catch {
        /* not a repo / no file API — the Branch row stays hidden */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return branch;
}

/* ------------------------------------------------------------------ */
/* shell — collapsible header + row pile                               */
/* ------------------------------------------------------------------ */

function Section({
  label,
  icon,
  chip,
  children,
  defaultOpen = true,
}: {
  label: string;
  icon: ReactNode;
  chip?: string | null;
  children?: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="sc-sec">
      <button
        type="button"
        className="sc-h"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
      >
        <span className="sc-h-chev" aria-hidden="true">
          {open ? (
            <ChevronDown size={12} strokeWidth={2} />
          ) : (
            <ChevronRight size={12} strokeWidth={2} />
          )}
        </span>
        <span className="sc-h-ic" aria-hidden="true">
          {icon}
        </span>
        <span className="sc-h-l">{label}</span>
        {chip ? <span className="sc-h-chip mono num">{chip}</span> : null}
      </button>
      {open && children ? <div className="sc-rows">{children}</div> : null}
    </div>
  );
}

function Row({
  icon,
  text,
  cls = "",
}: {
  icon?: ReactNode;
  text: string;
  cls?: string;
}) {
  return (
    <div className={`sc-row${cls ? ` ${cls}` : ""}`} role="listitem">
      {icon ? (
        <span className="sc-row-ic" aria-hidden="true">
          {icon}
        </span>
      ) : null}
      <span className="sc-row-l" title={text}>
        {text}
      </span>
    </div>
  );
}

/* one fold: collapsed "N completed" → open rows (+ the hide toggle)   */
function Fold({ items, kind }: { items: ZTTodoItem[]; kind: "done" | "wait" }) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  const word = kind === "done" ? "completed" : "waiting";
  return (
    <>
      <button
        type="button"
        className="sc-fold"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
      >
        {open ? `▾ Hide ${items.length} ${word}` : `▸ ${items.length} ${word}`}
      </button>
      {open &&
        items.map((t, i) => (
          <Row
            key={`${kind}-${i}`}
            cls="dim"
            icon={
              kind === "done" ? (
                <Check size={12} strokeWidth={2.2} />
              ) : (
                <CircleDot size={12} strokeWidth={1.8} />
              )
            }
            text={t.content}
          />
        ))}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* the card                                                            */
/* ------------------------------------------------------------------ */

export default function StatusCard() {
  const { turnId, model, running } = usePaneGoal();
  const branch = useBranch();
  const now = useNow(running);
  if (!turnId || !model) return null;

  const p = model.panel;
  const todo = p.todo?.items ?? [];
  const done = todo.filter(t => t.status === "completed");
  const active = todo.filter(t => t.status === "in_progress");
  const waiting = todo.filter(t => t.status === "pending");
  const score = todo.length > 0 ? `${done.length}/${todo.length}` : null;

  const startMs = p.startedAt !== null ? p.startedAt * 1000 : null;
  const endMs = p.endedAt !== null ? p.endedAt * 1000 : null;
  const elapsed =
    startMs !== null
      ? fmtShortDuration(
          Math.max(0, (running ? now : (endMs ?? Date.now())) - startMs)
        )
      : null;

  return (
    <div className="status-card" data-goal-running={running || undefined}>
      {(branch !== null || p.changesFiles > 0 || p.environmentTools) && (
        <Section
          label="Git tools"
          chip={p.changesFiles > 0 ? String(p.changesFiles) : null}
          icon={<Flag size={12} strokeWidth={1.8} />}
        >
          {p.changesFiles > 0 ? (
            <div className="sc-row" role="listitem">
              <span className="sc-row-ic" aria-hidden="true">
                <FilePen size={12} strokeWidth={1.8} />
              </span>
              <span className="sc-row-l">Changes</span>
              <span className="sc-row-num mono num add">+{p.changesFiles}</span>
              {p.changesTools > 0 && (
                <span className="sc-row-num mono num">
                  {p.changesTools} edits
                </span>
              )}
            </div>
          ) : (
            running && (
              <Row
                cls="dim"
                icon={<Check size={12} strokeWidth={2} />}
                text="Clean"
              />
            )
          )}
          {branch && (
            <div className="sc-row" role="listitem">
              <span className="sc-row-ic mono" aria-hidden="true">
                ⌥
              </span>
              <span className="sc-row-l">{branch}</span>
              <span className="sc-row-chev" aria-hidden="true">
                ⌄
              </span>
            </div>
          )}
          {p.environmentTools && (
            <button
              type="button"
              className="sc-row sc-row-btn"
              onClick={() =>
                toast("Git actions run through the task chat in this build.", {
                  description: "Ask the agent to commit or push your changes.",
                })
              }
            >
              <span className="sc-row-ic mono" aria-hidden="true">
                ⇥
              </span>
              <span className="sc-row-l">Commit or push</span>
            </button>
          )}
        </Section>
      )}

      {p.goal && p.goal.trim().length > 0 && (
        <Section
          label="Goal"
          chip={elapsed ?? undefined}
          icon={<Flag size={12} strokeWidth={1.5} />}
        >
          <Row
            icon={
              running ? (
                <span className="fl-spin">
                  <LoaderCircle size={12} strokeWidth={1.5} />
                </span>
              ) : p.outcome === "failed" || p.outcome === "cancelled" ? (
                <CircleDot size={12} strokeWidth={1.5} />
              ) : (
                <Check size={12} strokeWidth={1.5} />
              )
            }
            text={p.goal.length > 90 ? `${p.goal.slice(0, 90)}…` : p.goal}
          />
        </Section>
      )}

      {p.plans.length > 0 && (
        <Section label="Plans" icon={<Workflow size={12} strokeWidth={1.8} />}>
          {p.plans
            .filter(s => s.instruction && s.instruction !== p.goal)
            .slice(0, 4)
            .map((s, i) => (
              <Row
                key={`p-${i}`}
                icon={<List size={12} strokeWidth={1.8} />}
                text={
                  s.instruction.length > 64
                    ? `${s.instruction.slice(0, 64)}…`
                    : s.instruction
                }
              />
            ))}
        </Section>
      )}

      {todo.length > 0 && (
        <Section
          label="Progress"
          chip={score}
          icon={<ListChecks size={12} strokeWidth={1.8} />}
        >
          <Fold items={done} kind="done" />
          {active.map((t, i) => (
            <Row
              key={`a-${i}`}
              icon={
                <span className="fl-spin">
                  <LoaderCircle size={12} strokeWidth={2.2} />
                </span>
              }
              text={t.content}
            />
          ))}
          <Fold items={waiting} kind="wait" />
        </Section>
      )}

      {(p.agentsRunning.length > 0 || p.agentsEnded > 0) && (
        <Section
          label="Agents"
          chip={
            p.agentsRunning.length > 0
              ? `${p.agentsRunning.length} running`
              : `Ended ${p.agentsEnded}`
          }
          icon={<Bot size={12} strokeWidth={1.8} />}
        >
          {p.agentsRunning.map((name, i) => (
            <Row
              key={`ar-${i}`}
              icon={
                <span className="fl-spin">
                  <LoaderCircle size={12} strokeWidth={2.2} />
                </span>
              }
              text={name}
            />
          ))}
          {p.agentsEnded > 0 && (
            <Row cls="dim" text={`Ended ${p.agentsEnded}`} />
          )}
        </Section>
      )}

      {p.workflowsEnded > 0 && (
        <Section
          label="Workflows"
          chip={`Ended ${p.workflowsEnded}`}
          icon={<Workflow size={12} strokeWidth={1.8} />}
        >
          <Row cls="dim" text="Ended workflows" />
        </Section>
      )}

      {p.terminals > 0 && (
        <Section
          label="Terminals"
          chip={String(p.terminals)}
          icon={<SquareTerminal size={12} strokeWidth={1.8} />}
        />
      )}
    </div>
  );
}
