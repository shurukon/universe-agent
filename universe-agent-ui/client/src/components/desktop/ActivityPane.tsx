/**
 * components/desktop/ActivityPane.tsx — the Rail's Activity tab
 * (UI-STATUS-MASTER-SPEC §D): the live agent tree of the goal on screen —
 * plan checklist, one row per agent (calm per-agent tint, state, elapsed),
 * tool calls as foldable sub-rows (queued/running/done/failed with the
 * real error), handoffs as thin arrows, skill badges, and a recap when
 * the goal ends. Consumes the SAME pure fold the in-chat flow renders
 * (lib/flow/fold.ts) — one source of truth, two surfaces.
 */
import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Check,
  ChevronDown,
  CircleDot,
  LoaderCircle,
  Sparkles,
  X,
} from "lucide-react";
import { useChat } from "@/stores/chat";
import { useLiveGoalId } from "@/hooks/useLiveGoalId";
import { foldFlow, fmtDur, FAMILY_LABEL } from "@/lib/flow/fold";
import { agentTintDeg, tintFilter } from "@/lib/flow/agentTint";
import type { FlowAgent, FlowModel, FlowToolCall } from "@/lib/flow/fold";

function AgentGlyph({ name }: { name: string }) {
  return (
    <span
      className="act-glyph"
      style={{ filter: tintFilter(agentTintDeg(name)) }}
      aria-hidden="true"
    >
      ◆
    </span>
  );
}

function StateMark({ state }: { state: FlowAgent["state"] }) {
  if (state === "working")
    return (
      <span className="fl-spin act-spin" aria-hidden="true">
        <LoaderCircle size={11} strokeWidth={1.5} />
      </span>
    );
  if (state === "done")
    return (
      <Check
        size={11}
        strokeWidth={1.5}
        className="act-ok"
        aria-hidden="true"
      />
    );
  if (state === "failed")
    return (
      <X size={11} strokeWidth={1.5} className="act-err" aria-hidden="true" />
    );
  return (
    <CircleDot
      size={11}
      strokeWidth={1.5}
      className="act-wait"
      aria-hidden="true"
    />
  );
}

function elapsed(a: FlowAgent, now: number): string | null {
  const start = a.startedAt;
  if (!start) return null;
  const end = a.endedAt ?? (a.state === "working" ? now : null);
  if (!end) return null;
  return fmtDur((end - start) * 1000);
}

function ToolRow({ call }: { call: FlowToolCall }) {
  const [open, setOpen] = useState(false);
  const stateCls =
    call.state === "completed"
      ? "done"
      : call.state === "error"
        ? "error"
        : call.state === "running"
          ? "running"
          : call.state === "blocked"
            ? "blocked"
            : "queued";
  return (
    <div className={`act-tool ${stateCls}`}>
      <button
        type="button"
        className="act-tool-row"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        title={call.tool}
      >
        <span className="act-tool-fam mono">{FAMILY_LABEL[call.family]}</span>
        <span className="act-tool-name mono">{call.tool}</span>
        <span className="sp" />
        {call.state === "running" ? (
          <span className="fl-spin act-spin" aria-hidden="true">
            <LoaderCircle size={10} strokeWidth={1.5} />
          </span>
        ) : call.state === "completed" ? (
          <Check
            size={10}
            strokeWidth={1.5}
            className="act-ok"
            aria-hidden="true"
          />
        ) : call.state === "error" || call.state === "blocked" ? (
          <X
            size={10}
            strokeWidth={1.5}
            className="act-err"
            aria-hidden="true"
          />
        ) : null}
        {call.durationMs != null && (
          <span className="act-dur mono">{fmtDur(call.durationMs)}</span>
        )}
      </button>
      {call.error && (
        <div className="act-tool-error" title={call.error}>
          {call.error.slice(0, 240)}
        </div>
      )}
      {open && (
        <div className="act-tool-args mono" title="arguments">
          {call.args?.slice(0, 600) || "—"}
        </div>
      )}
    </div>
  );
}

function AgentBlock({ agent, now }: { agent: FlowAgent; now: number }) {
  const [open, setOpen] = useState(true);
  const dur = elapsed(agent, now);
  return (
    <div className={`act-agent ${agent.state}`}>
      <div className="act-agent-row">
        <AgentGlyph name={agent.name} />
        <span className="act-agent-name">{agent.name}</span>
        <StateMark state={agent.state} />
        <span className="sp" />
        {dur && <span className="act-dur mono">{dur}</span>}
        {agent.tools.length > 0 && (
          <button
            type="button"
            className="act-fold"
            onClick={() => setOpen(v => !v)}
            aria-expanded={open}
            aria-label={`${open ? "Collapse" : "Expand"} ${agent.name}'s tools`}
          >
            <ChevronDown
              size={11}
              strokeWidth={2}
              style={{ transform: open ? "" : "rotate(-90deg)" }}
            />
          </button>
        )}
      </div>
      {agent.summary && (
        <div className="act-agent-summary">{agent.summary}</div>
      )}
      {open && agent.tools.length > 0 && (
        <div className="act-tools">
          {agent.tools.map(tc => (
            <ToolRow key={tc.key} call={tc} />
          ))}
        </div>
      )}
    </div>
  );
}

function Recap({ model }: { model: FlowModel }) {
  // whole seconds like ZCode's summary chips — sub-second detail lives on
  // the per-tool rows below, not in the recap line
  const durS =
    model.startedAt && model.endedAt
      ? `${Math.max(1, Math.round(model.endedAt - model.startedAt))}s`
      : null;
  return (
    <div className="act-recap" role="status">
      {model.outcome === "failed" ? (
        <X size={12} strokeWidth={2.4} className="act-err" aria-hidden="true" />
      ) : (
        <Check
          size={12}
          strokeWidth={2.4}
          className="act-ok"
          aria-hidden="true"
        />
      )}
      <b>
        {model.outcome === "failed"
          ? "failed"
          : model.outcome === "cancelled"
            ? "stopped"
            : "done"}
      </b>
      {durS && <span className="mono">{durS}</span>}
      <span>· {model.agents.length} agents</span>
      <span>· {model.toolCount} tools</span>
      {model.skills.length > 0 && <span>· {model.skills.length} skills</span>}
      {model.handoffs.length > 0 && (
        <span>· {model.handoffs.length} handoffs</span>
      )}
      {model.artifacts.length > 0 && (
        <span>· {model.artifacts.length} files</span>
      )}
    </div>
  );
}

export default function ActivityPane() {
  const turnId = useLiveGoalId();
  const events = useChat(s => (turnId ? s.eventsByGoal[turnId] : undefined));
  const consents = useChat(s => s.consents.length);
  // ticking "now" so working elapsed counters move; pauses when idle
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = useMemo(() => {
    if (!events) return false;
    return (
      events.some(
        e => e.type === "turn_started" || e.type === "turn_started"
      ) &&
      !events.some(
        e =>
          e.type === "turn_completed" ||
          e.type === "turn_failed" ||
          e.type === "turn_cancelled" ||
          e.type === "turn_completed" ||
          e.type === "turn_failed" ||
          e.type === "turn_cancelled"
      )
    );
  }, [events]);
  useEffect(() => {
    if (!running) return;
    const iv = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(iv);
  }, [running]);

  const model = useMemo(
    () =>
      events && events.length
        ? foldFlow(events, { consentsOpen: consents })
        : null,
    [events, consents]
  );

  if (!model || (model.agents.length === 0 && model.toolCount === 0)) {
    return (
      <div className="rail-pane">
        <div className="rail-pane-head">
          <span className="rail-pane-title mono">Activity</span>
        </div>
        <div className="prog-empty">
          No live activity yet — agent runs, their tool calls and results land
          here in real time, and the pane opens by itself when agents start
          working.
        </div>
      </div>
    );
  }

  return (
    <div className="rail-pane">
      <div className="rail-pane-head">
        <span className="rail-pane-title mono">
          Activity{running ? " · live" : ""}
        </span>
      </div>

      {model.outcome && <Recap model={model} />}

      {model.plan && model.plan.length > 0 && (
        <div className="act-plan" aria-label="Plan">
          {model.plan.map((p, i) => (
            <div className="act-plan-step" key={i}>
              <span className="mono act-plan-idx">{p.step}</span>
              <span className="act-plan-agent mono">{p.agent}</span>
              <span className="act-plan-txt">{p.instruction}</span>
            </div>
          ))}
        </div>
      )}

      <div className="act-agents" role="list" aria-label="Agents">
        {model.agents.map(a => (
          <div role="listitem" key={a.name}>
            <AgentBlock agent={a} now={now} />
          </div>
        ))}
      </div>

      {model.handoffs.length > 0 && (
        <div className="act-handoffs" aria-label="Handoffs">
          {model.handoffs.map((h, i) => (
            <div className="act-handoff" key={i} title={h.what}>
              <span className="mono">{h.from}</span>
              <ArrowRight size={10} strokeWidth={2} aria-hidden="true" />
              <span className="mono">{h.to}</span>
              {h.what && <span className="act-handoff-what">{h.what}</span>}
            </div>
          ))}
        </div>
      )}

      {model.skills.length > 0 && (
        <div className="act-skills" aria-label="Skills used">
          <Sparkles size={11} strokeWidth={1.8} aria-hidden="true" />
          {model.skills.map(s => (
            <span key={s} className="act-skill mono">
              {s}
            </span>
          ))}
        </div>
      )}

      {model.artifacts.length > 0 && (
        <div className="act-artifacts" aria-label="Produced files">
          {model.artifacts.map((a, i) => (
            <div className="act-artifact mono" key={i} title={a.path}>
              {a.path.split(/[\\/]/).pop()}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
