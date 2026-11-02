/**
 * lib/flow/fold.ts — the single event-fold for the M1 in-chat flow.
 *
 * Turns a goal's raw BridgeEvent stream into a chronological model the
 * chat surface renders directly: brain (thinking) info, per-agent segments
 * with their tool calls, handoffs, the live status line, and outcome.
 * Pure logic, no rendering — AgentFlow.tsx consumes this.
 */
import type { BridgeEvent } from "@/lib/api";

export type FlowToolState =
  "pending" | "running" | "completed" | "error" | "blocked";

/** ZCode tool-family groups (Phase 2 item 3): consecutive calls of the
 *  same family fold into one row — "Explore · 6 events · 2 messages". */
export type ToolFamily = "explore" | "terminal" | "changes" | "other";

export function toolFamily(tool: string): ToolFamily {
  const lower = tool.toLowerCase();
  if (
    tool === "Read" ||
    lower === "file.read" ||
    lower === "file.list" ||
    tool === "LS" ||
    tool === "Glob" ||
    tool === "Grep" ||
    tool === "ImageOcr" ||
    lower === "image_ocr" ||
    tool === "VisionAnalyze" ||
    lower === "vision_analyze" ||
    tool === "WebSearch" ||
    tool === "WebFetch" ||
    tool === "WebResearch" ||
    tool.startsWith("Browser") ||
    tool === "MemoryQuery" ||
    tool.startsWith("arxiv_") ||
    tool.startsWith("wikipedia_") ||
    tool === "HttpClient" ||
    tool.startsWith("Openapi") ||
    tool.startsWith("Graphql") ||
    tool.startsWith("mcp_") ||
    tool === "StaticAnalysis" ||
    tool === "VisionAnalyze" ||
    tool === "ImageOcr"
  )
    return "explore";
  if (
    tool === "Bash" ||
    tool === "BashUnsandboxed" ||
    tool === "CodeExecute" ||
    tool === "LogTail" ||
    tool.startsWith("Docker") ||
    tool === "K8s" ||
    tool === "Cicd" ||
    tool === "Ssh" ||
    tool.startsWith("TestRunner") ||
    tool === "Sql" ||
    tool === "PandasQuery"
  )
    return "terminal";
  if (
    tool === "Write" ||
    tool === "Edit" ||
    tool === "Delete" ||
    tool.startsWith("Git") ||
    tool.startsWith("github_") ||
    tool === "SvgGen" ||
    tool === "CssTools" ||
    tool.startsWith("Charting")
  )
    return "changes";
  return "other";
}

export const FAMILY_LABEL: Record<ToolFamily, string> = {
  explore: "Explore",
  terminal: "Terminal",
  changes: "Changes",
  other: "Tools",
};

export interface FlowToolCall {
  key: string;
  tool: string;
  agent: string | null;
  args: string;
  state: FlowToolState;
  durationMs: number | null;
  error: string | null;
  result: string | null;
  ts: number;
  /** ZCode row: how many bridge events fed this call (requested, running,
   *  finished/failed, blocked, hook intercepts) */
  events: number;
  /** hook_intercepted rows this call produced (the Hooks badge) */
  hooks: string[];
  family: ToolFamily;
}

export type FlowAgentState = "working" | "done" | "failed" | "waiting";

export interface FlowAgent {
  name: string;
  state: FlowAgentState;
  startedAt: number | null;
  endedAt: number | null;
  tools: FlowToolCall[];
  summary: string | null;
  /** subagent_spawned (S2) — the mission brief this branch runs */
  brief: string | null;
}

export interface FlowHandoff {
  from: string;
  to: string;
  what: string;
}

export interface FlowBrain {
  rounds: number;
  instruction: string | null;
  handoffIn: string | null;
  /** live streamed thinking (llm_delta events), last ~4KB */
  text: string;
  /** measured thinking time (ZCode Thought duration): the summed spans
   *  between the goal start / end of a tool call and the next tool/agent
   *  event — real event timestamps, never wall-clock guesses */
  thinkMs: number;
}

/** one produced file (from the live artifact_created stream) */
export interface FlowArtifact {
  path: string;
  bytes: number | null;
  tool: string;
  ts: number;
}

export type StatusKind =
  "working" | "thinking" | "consent" | "error" | "done" | "stopped";

export interface FlowStatus {
  kind: StatusKind;
  /** the agent currently working, or null for orchestrator-level states */
  agent: string | null;
  /** the one live line beside the universe: specific, present tense */
  text: string;
}

export type FlowOutcome = "completed" | "failed" | "cancelled" | null;

export interface FlowModel {
  usesAgents: boolean;
  agents: FlowAgent[];
  brain: FlowBrain | null;
  handoffs: FlowHandoff[];
  skills: string[];
  plan: NonNullable<BridgeEvent["plan"]> | null;
  activeAgent: string | null;
  status: FlowStatus;
  outcome: FlowOutcome;
  startedAt: number | null;
  endedAt: number | null;
  providerHalt: string | null;
  browserActive: boolean;
  toolCount: number;
  artifacts: FlowArtifact[];
}

/** Internal plumbing the user never sees as "work". */
/** Internal plumbing the user never sees as "work". ZCode's Skill tool is
 *  agent-visible (it chooses a skill before working), so nothing is hidden
 *  by name anymore — the set stays as the seam if a hidden tool returns. */
const HIDDEN_TOOLS = new Set<string>();

/** A goal "invokes agents" when its stream carries agent lifecycle. */
export function goalInvokesAgents(events: BridgeEvent[]): boolean {
  return events.some(
    e =>
      e.type === "agent_started" ||
      e.type === "handoff" ||
      e.type === "turn_started" ||
      e.type === "turn_planned" || // legacy mirrors
      e.type === "tool_running"
  );
}

/** Browser-tab activity: drives the Rail auto-opening its Browser pane. */
export function goalHasBrowserActivity(events: BridgeEvent[]): boolean {
  return events.some(
    e =>
      typeof e.tool === "string" &&
      e.tool.startsWith("browser_") &&
      (e.type === "tool_running" ||
        e.type === "tool_finished" ||
        e.type === "tool_failed")
  );
}

export function fmtDur(ms: number): string {
  const s = Math.max(ms / 1000, 0);
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
}

export function foldFlow(
  events: BridgeEvent[],
  opts?: { consentsOpen?: number }
): FlowModel {
  const consentsOpen = opts?.consentsOpen ?? 0;
  const order: string[] = [];
  const byName = new Map<string, FlowAgent>();
  const handoffs: FlowHandoff[] = [];
  const skills: string[] = [];
  const artifacts: FlowArtifact[] = [];
  const artifactIdx = new Map<string, number>();
  let plan: NonNullable<BridgeEvent["plan"]> | null = null;
  let outcome: FlowOutcome = null;
  let startedAt: number | null = null;
  let endedAt: number | null = null;
  let providerHalt: string | null = null;
  let failed = false;
  let current: string | null = null;
  let toolCount = 0;

  // thinking provenance (the brain row shows what is actually available)
  let rounds = 0;
  let stepInstruction: string | null = null;
  let handoffIn: string | null = null;
  // live streamed thinking (M5): llm_delta accumulation, capped at ~4KB
  let brainText = "";
  // measured thinking spans (ZCode Thought duration): an llm event opens a
  // span; the first non-llm work event (tool/agent/terminal) closes it
  let thinkMs = 0;
  let thinkOpenTs: number | null = null;
  // open tool calls keyed for close-matching
  const openByTool = new Map<string, FlowToolCall>();

  const ensure = (name: string): FlowAgent => {
    let a = byName.get(name);
    if (!a) {
      a = {
        name,
        state: "waiting",
        startedAt: null,
        endedAt: null,
        tools: [],
        summary: null,
        brief: null,
      };
      byName.set(name, a);
      order.push(name);
    }
    return a;
  };

  for (const ev of events) {
    switch (ev.type) {
      case "turn_started":
      case "turn_started": // legacy mirrors
        startedAt = ev.ts;
        break;
      case "turn_planned":
        if (ev.plan) plan = ev.plan;
        break;
      case "provider_halt":
        providerHalt = ev.reason ?? ev.error ?? "provider unavailable";
        failed = true;
        break;
      case "turn_failed":
      case "turn_failed": // legacy mirrors
        failed = true;
        outcome = "failed";
        endedAt = ev.ts;
        if (thinkOpenTs !== null) {
          thinkMs += Math.max(0, (ev.ts - thinkOpenTs) * 1000);
          thinkOpenTs = null;
        }
        break;
      case "turn_completed":
      case "turn_completed": // legacy mirrors
        outcome = "completed";
        endedAt = ev.ts;
        if (thinkOpenTs !== null) {
          thinkMs += Math.max(0, (ev.ts - thinkOpenTs) * 1000);
          thinkOpenTs = null;
        }
        break;
      case "turn_cancelled":
      case "turn_cancelled": // legacy mirrors
        outcome = "cancelled";
        endedAt = ev.ts;
        if (thinkOpenTs !== null) {
          thinkMs += Math.max(0, (ev.ts - thinkOpenTs) * 1000);
          thinkOpenTs = null;
        }
        break;
      case "subagent_spawned": {
        // S2: a mission branch opens — colored by role in the flow while
        // the trailing universe keeps its identity color
        if (ev.agent) {
          if (thinkOpenTs !== null) {
            thinkMs += Math.max(0, (ev.ts - thinkOpenTs) * 1000);
            thinkOpenTs = null;
          }
          const a = ensure(ev.agent);
          a.state = "working";
          a.startedAt = ev.ts;
          if (typeof ev.brief === "string" && ev.brief)
            a.brief = ev.brief.slice(0, 240);
          current = ev.agent;
        }
        break;
      }
      case "subagent_complete": {
        if (ev.agent) {
          const a = ensure(ev.agent);
          a.state = ev.success === false ? "failed" : "done";
          a.endedAt = ev.ts;
          if (typeof ev.summary === "string" && ev.summary)
            a.summary = ev.summary.slice(0, 240);
          if (current === ev.agent) current = null;
        }
        break;
      }
      case "agent_started":
        if (ev.agent) {
          // delegation is work too: close the thinking span here (without
          // this, thinkMs over-counts when the model hands off without a
          // visible tool call)
          if (thinkOpenTs !== null) {
            thinkMs += Math.max(0, (ev.ts - thinkOpenTs) * 1000);
            thinkOpenTs = null;
          }
          const a = ensure(ev.agent);
          a.state = "working";
          a.startedAt = ev.ts;
          current = ev.agent;
          if (typeof ev.detail === "string" && ev.detail)
            stepInstruction = ev.detail.slice(0, 220);
        }
        break;
      case "agent_complete":
        if (ev.agent) {
          const a = ensure(ev.agent);
          a.state = failed ? "failed" : "done";
          a.endedAt = ev.ts;
          a.summary =
            typeof ev.detail === "string" && ev.detail
              ? ev.detail.slice(0, 220)
              : (ev.what_was_done ?? null);
          if (current === ev.agent) current = null;
        }
        break;
      case "llm_round":
        rounds = Math.max(rounds, (ev.round_index ?? rounds) + 1);
        if (thinkOpenTs === null) thinkOpenTs = ev.ts;
        break;
      case "llm_delta": {
        if (typeof ev.text === "string" && ev.text)
          brainText = (brainText + ev.text).slice(-4000);
        if (thinkOpenTs === null) thinkOpenTs = ev.ts;
        break;
      }
      case "tool_requested":
      case "tool_running": {
        if (!ev.tool || HIDDEN_TOOLS.has(ev.tool)) break;
        // work begins: the thinking span is closed here (ZCode Thought
        // measures the silent gap before the next visible action)
        if (thinkOpenTs !== null) {
          thinkMs += Math.max(0, (ev.ts - thinkOpenTs) * 1000);
          thinkOpenTs = null;
        }
        // S2: a subagent's tool events carry its own role — attribute the
        // row to its branch, not to the universe agent that spawned it
        // ("general" is the pre-v22 label, kept for old rows)
        const owner =
          ev.agent && ev.agent !== "universe" && ev.agent !== "general"
            ? ev.agent
            : (current ?? ev.agent ?? null);
        if (ev.type === "tool_running") toolCount += 1;
        const open = openByTool.get(ev.tool);
        if (open && (open.state === "pending" || open.state === "running")) {
          open.events += 1;
          if (ev.type === "tool_running") open.state = "running";
          if (ev.type === "tool_running" && ev.args_summary)
            open.args = ev.args_summary;
        } else {
          const call: FlowToolCall = {
            key: `call:${ev.tool}:${toolCount}:${openByTool.size}`,
            tool: ev.tool,
            agent: owner,
            args: ev.args_summary ?? "",
            state: ev.type === "tool_running" ? "running" : "pending",
            durationMs: null,
            error: null,
            result: null,
            ts: ev.ts,
            events: 1,
            hooks: [],
            family: toolFamily(ev.tool),
          };
          openByTool.set(ev.tool, call);
          if (owner) ensure(owner).tools.push(call);
        }
        if (owner) {
          const a = ensure(owner);
          if (a.state === "waiting") {
            a.state = "working";
            a.startedAt = ev.ts;
          }
        }
        break;
      }
      case "tool_finished":
      case "tool_failed": {
        if (!ev.tool) break;
        const open = openByTool.get(ev.tool);
        if (open) {
          open.events += 1;
          open.state = ev.type === "tool_finished" ? "completed" : "error";
          open.durationMs = ev.duration_ms ?? null;
          if (ev.type === "tool_finished")
            open.result = ev.detail ?? ev.args_summary ?? null;
          else {
            open.error = ev.error ?? "failed";
            failed = true;
          }
          openByTool.delete(ev.tool);
        } else {
          // History replay: standalone tool_finished without prior open
          const ownerHist =
            (ev as unknown as { agent?: string }).agent &&
            (ev as unknown as { agent?: string }).agent !== "universe" &&
            (ev as unknown as { agent?: string }).agent !== "general"
              ? ((ev as unknown as { agent?: string }).agent ?? null)
              : (current ??
                (ev as unknown as { agent?: string }).agent ??
                null);
          const call: FlowToolCall = {
            key: `hist:${ev.tool}:${ev.seq ?? Date.now()}`,
            tool: ev.tool,
            agent: ownerHist,
            args:
              (ev.args_summary as string) ??
              (ev as unknown as { args?: string }).args ??
              "",
            state: ev.type === "tool_finished" ? "completed" : "error",
            durationMs:
              (ev as unknown as { duration_ms?: number }).duration_ms ?? null,
            error:
              ev.type === "tool_failed"
                ? ((ev as unknown as { error?: string }).error ?? "failed")
                : null,
            result:
              ev.type === "tool_finished"
                ? ((ev as unknown as { detail?: string }).detail ??
                  (ev as unknown as { result_preview?: string })
                    .result_preview ??
                  null)
                : null,
            ts: ev.ts,
            events: 1,
            hooks: [],
            family: ev.tool ? toolFamily(ev.tool) : "other",
          };
          ensure(ownerHist ?? "universe").tools.push(call);
          if (ev.type === "tool_failed") failed = true;
        }
        break;
      }
      case "tool_blocked": {
        if (!ev.tool) break;
        const open = openByTool.get(ev.tool);
        if (open) {
          open.state = "blocked";
          open.events += 1;
        }
        break;
      }
      case "hook_intercepted": {
        // the Hooks badge: attribute the intercept to the open call of the
        // same tool (the hook fires between requested and running)
        if (ev.tool) {
          const open = openByTool.get(ev.tool);
          const label = `${ev.hook_id ?? "hook"} · ${ev.action ?? "block"}${
            ev.message ? ` — ${ev.message.slice(0, 120)}` : ""
          }`;
          if (open) {
            open.hooks.push(label);
            open.events += 1;
          }
        }
        break;
      }
      case "handoff": {
        if (ev.from_agent && ev.to_agent) {
          if (thinkOpenTs !== null) {
            thinkMs += Math.max(0, (ev.ts - thinkOpenTs) * 1000);
            thinkOpenTs = null;
          }
          handoffs.push({
            from: ev.from_agent,
            to: ev.to_agent,
            what: ev.what_was_done ?? "",
          });
          current = ev.to_agent;
          const a = ensure(ev.to_agent);
          if (a.state === "waiting") {
            a.state = "working";
            a.startedAt = ev.ts;
          }
          handoffIn = (ev.what_was_done ?? "").slice(0, 220) || null;
        }
        break;
      }
      case "skill_activated": {
        const name = ev.skill_name ?? ev.skill;
        if (name && !skills.includes(name)) skills.push(name);
        break;
      }
      case "artifact_created": {
        // live artifacts (file.write/file.edit): one chip per path — a
        // rewrite of the same file updates its chip, not duplicates it
        const path = typeof ev.path === "string" ? ev.path : "";
        if (!path) break;
        const next: FlowArtifact = {
          path,
          bytes: typeof ev.bytes === "number" && ev.bytes > 0 ? ev.bytes : null,
          tool: ev.tool ?? "",
          ts: ev.ts,
        };
        const idx = artifactIdx.get(path);
        if (idx !== undefined) artifacts[idx] = next;
        else {
          artifacts.push(next);
          artifactIdx.set(path, artifacts.length - 1);
        }
        break;
      }
      case "turn_replanned": {
        if (ev.plan) plan = ev.plan;
        break;
      }
      case "steer_received":
      case "steer_queued":
        break;
      default:
        break;
    }
  }

  const agents = order.map(n => byName.get(n)!);
  if (failed) {
    for (const a of agents) if (a.state === "working") a.state = "failed";
  }

  const brain: FlowBrain | null =
    rounds > 0 || stepInstruction || brainText || thinkMs > 0
      ? {
          rounds,
          instruction: stepInstruction,
          handoffIn,
          text: brainText,
          thinkMs,
        }
      : null;

  const activeAgent = current;
  const browserActive = goalHasBrowserActivity(events);

  // ---- the one live status line (specific, present tense) ----
  let status: FlowStatus;
  if (providerHalt) {
    status = { kind: "error", agent: null, text: providerHalt };
  } else if (consentsOpen > 0) {
    status = {
      kind: "consent",
      agent: current,
      text: "needs your approval",
    };
  } else if (outcome === "completed") {
    status = { kind: "done", agent: null, text: "done" };
  } else if (outcome === "failed") {
    status = { kind: "error", agent: null, text: "did not complete" };
  } else if (outcome === "cancelled") {
    status = { kind: "stopped", agent: null, text: "stopped" };
  } else {
    // walk back to the most meaningful live event
    let found: FlowStatus | null = null;
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev.type === "tool_running" && ev.tool) {
        const owner = ev.agent ?? current;
        found = {
          kind: "working",
          agent: owner,
          text: owner ? `${owner} — ${ev.tool}` : ev.tool,
        };
        break;
      }
      if (ev.type === "agent_started" && ev.agent) {
        found = { kind: "working", agent: ev.agent, text: ev.agent };
        break;
      }
      if (ev.type === "llm_round") {
        found = {
          kind: "thinking",
          agent: current,
          text: current ? `${current} — thinking` : "thinking",
        };
        break;
      }
      if (ev.type === "consent_required") {
        found = {
          kind: "consent",
          agent: current,
          text: "needs your approval",
        };
        break;
      }
      if (ev.type === "turn_planned") {
        found = {
          kind: "thinking",
          agent: null,
          text: "universe is planning…",
        };
        break;
      }
      if (
        ev.type === "steer_received" ||
        ev.type === "steer_queued" ||
        ev.type === "turn_started" ||
        ev.type === "turn_replanned"
      ) {
        found = {
          kind: "thinking",
          agent: null,
          text: "orchestrator re-planning…",
        };
        break;
      }
    }
    status = found ?? {
      kind: "thinking",
      agent: null,
      text: "universe is thinking…",
    };
  }

  return {
    usesAgents: goalInvokesAgents(events),
    agents,
    brain,
    handoffs,
    skills,
    plan,
    activeAgent,
    status,
    outcome,
    startedAt,
    endedAt,
    providerHalt,
    browserActive,
    toolCount,
    artifacts,
  };
}
