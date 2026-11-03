/**
 * lib/flow/ztimeline.ts — the shell-parity fold (full-parity plan P1).
 *
 * Folds Universe's raw BridgeEvent stream into the Universe timeline model so
 * the chat surface renders EXACTLY the Universe semantics:
 *
 *   · reasoning rows  — "Thinking" (live, in_progress) that settle into
 *     "Thought" + duration ("a few seconds" / "{seconds} seconds"); the
 *     span opens on the first LLM event and closes at the first visible
 *     work event (E03, re-verified on 3.14.3 via IntlProvider keys
 *     chat.reasoning.thinking/thought/durationFewSeconds/durationSeconds)
 *   · tool rows — data-tool-call row semantics: in_progress → completed /
 *     failed / interrupted, family folding of CONSECUTIVE same-family
 *     calls (Explore / Terminal / Changes groups with "{count} events")
 *   · SubAgent rows — agent branches nest their own tool rows (Universe's
 *     parentToolUseId scoping; Universe scopes by ev.agent)
 *   · compaction separator — context_compaction events map to the "Context
 *     compressed …" separator row (Universe has none today — supported
 *     defensively); context_usage meter feeds the status panel instead
 *   · status panel — the chat.statusPanel.* sections: Goal, Agents
 *     ({count} running / Ended), Progress (todo folds), Changes, Terminals,
 *     Commit / Push, Git tools, Workflows — labels verbatim (i18n-en.json)
 *   · stream statuses — in_progress / completed / failed / interrupted
 *
 * Pure logic. components/flow/UniverseFlow.tsx (the message card) and
 * components/desktop/StatusStrip.tsx (the pinned panel) render this.
 */
import type { BridgeEvent } from "@/lib/api";

export type ZTStatus = "in_progress" | "completed" | "failed" | "interrupted";

/** Universe tool-family groups: consecutive calls of the same family fold
 *  into ONE row ("Explore · 8 events"). Matches the 3.11.x runtime and the
 *  3.14.3 strings (chat.toolCall.changesGroup.*). */
export type ToolFamily = "explore" | "terminal" | "changes" | "other";

export function toolFamily(tool: string): ToolFamily {
  const lower = tool.toLowerCase();
  // explore / reads: the read-side of the registry (Read, LS, Glob, Grep,
  // vision) — Universe renders each as its own one-line row or the merged
  // "Explore · N events" group, all with the lucide search glyph
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
    tool.startsWith("fetch") ||
    tool.startsWith("search") ||
    tool.startsWith("Gmail") ||
    tool.startsWith("Calendar") ||
    tool.startsWith("Drive") ||
    tool.startsWith("Sheets") ||
    tool.startsWith("Slide") ||
    tool === "RSS" ||
    tool.startsWith("rss_") ||
    tool.startsWith("news_")
  ) {
    return "explore";
  }
  // terminal / shell surface: the exact registry names the fold gets
  // (Bash, BashUnsandboxed, CodeExecute, shell.exec) plus the legacy
  // prefixes — Universe's "Terminal" row
  if (
    tool.startsWith("Bash") ||
    tool === "CodeExecute" ||
    lower === "code_execute" ||
    lower.startsWith("shell") ||
    lower.startsWith("terminal") ||
    lower.startsWith("process") ||
    tool.startsWith("Terminal") ||
    tool.startsWith("Shell") ||
    tool === "WindowsControl" ||
    tool.startsWith("app_") ||
    tool.startsWith("App") ||
    tool === "ComputerUse" ||
    tool === "PackageManager" ||
    tool === "Screenshot"
  ) {
    return "terminal";
  }
  if (
    tool.startsWith("file.") ||
    tool.startsWith("File") ||
    tool.startsWith("edit") ||
    tool.startsWith("Edit") ||
    tool.startsWith("write") ||
    tool.startsWith("Write") ||
    tool === "Delete" ||
    lower === "file.delete" ||
    tool.startsWith("patch") ||
    tool.startsWith("Patch") ||
    tool.startsWith("apply") ||
    tool === "Docx" ||
    tool === "Pptx" ||
    tool === "Xlsx" ||
    lower === "docx" ||
    lower === "pdf" ||
    lower === "pptx" ||
    lower === "xlsx"
  ) {
    return "changes";
  }
  return "other";
}

export interface ZTEntry {
  id: string;
  kind: "reasoning" | "tool" | "agent" | "compaction";
  status: ZTStatus;
  /** tool name (tool rows) / agent name (agent rows) */
  name: string | null;
  family: ToolFamily;
  /** SubAgent scoping: rows belonging to an agent branch nest under it */
  parentAgent: string | null;
  startedAt: number;
  endedAt: number | null;
  /** raw events folded into this row */
  events: number;
  /** tool calls folded by family into this row */
  groupCount: number;
  /** latest args_summary of the last folded call */
  args: string | null;
  /** result / output summary (close events) */
  detail: string | null;
  error: string | null;
  /** a tool_blocked landed while the call is open (permission wait) */
  blocked: boolean;
  /** hook intercepts attached while the call was open */
  hooks: string[];
  /** accumulated thinking text (reasoning rows, capped) */
  thought: string | null;
  /** duration accumulation for closed tool rows (sum of call durations) */
  durationMs: number;
  /** artifact paths attached (changes rows / chips) */
  files: string[];
  /** compaction extras */
  tokens: number | null;
  windowTokens: number | null;
}

export interface ZTTodoItem {
  content: string;
  status: string;
}

export interface ZTPlanStep {
  step: number;
  agent: string;
  instruction: string;
}

export interface ZTPanel {
  running: boolean;
  outcome: "completed" | "failed" | "cancelled" | null;
  goal: string | null;
  /** the current plan doc — every turn_planned replaces it (\[Universe Plans
   *  section shows these instructions, one row each]). */
  plans: ZTPlanStep[];
  agentsRunning: string[];
  agentsEnded: number;
  workflowsEnded: number;
  todo: { items: ZTTodoItem[]; idx: number | null } | null;
  changesFiles: number;
  changesTools: number;
  terminals: number;
  /** events never carry the branch name — the section is hidden until a
   *  future wire field fills it (Universe Branch section stays conditional) */
  branch: string | null;
  /** true when the panel should show "Changes · Clean" */
  clean: boolean;
  /** git tools seen (Commit / Push + Git tools sections) */
  environmentTools: boolean;
  contextUsage: {
    used: number | null;
    total: number | null;
    estimate: boolean;
  };
  providerHalt: string | null;
  startedAt: number | null;
  endedAt: number | null;
  toolCount: number;
}

export interface ZTShimStatus {
  kind: "working" | "thinking" | "done" | "error" | "stopped" | "consent";
  agent: string | null;
  text: string;
}

export interface ZTModel {
  entries: ZTEntry[];
  panel: ZTPanel;
  /** the one live line for the trailing universe (same contract as FlowStatus) */
  status: ZTShimStatus;
  toolCount: number;
}

export interface ZTFoldOpts {
  consentsOpen?: number;
  /** the owning task's text — fills the panel Goal section */
  taskText?: string | null;
}

/* ------------------------------------------------------------------ */
/* Universe duration formatting (IntlProvider 3.14.3 keys, en strings)     */
/* ------------------------------------------------------------------ */

export function fmtDurationMs(ms: number): string {
  const s = ms / 1000;
  if (s < 1) return "under a second";
  if (s < 7) return "a few seconds";
  const r = Math.round(s);
  if (r < 60) return r === 1 ? "1 second" : `${r} seconds`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s % 60);
  if (m < 60) return `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/** the measured span "a few seconds"/"{n} seconds" for row sublabels */
export function fmtShortDuration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

/* ------------------------------------------------------------------ */
/* the fold                                                             */
/* ------------------------------------------------------------------ */

const THOUGHT_CAP = 4000;
const MAIN_AGENT = new Set(["universe", "general"]);

export function foldZTimeline(
  events: BridgeEvent[],
  opts: ZTFoldOpts = {}
): ZTModel {
  const consentsOpen = opts.consentsOpen ?? 0;
  const entries: ZTEntry[] = [];
  const byId = new Map<string, ZTEntry>();
  const insertion: ZTEntry[] = [];

  let seq = 0;
  const push = (
    kind: ZTEntry["kind"],
    parentAgent: string | null,
    init: {
      name?: string | null;
      family?: ToolFamily;
      status?: ZTStatus;
      args?: string | null;
      thought?: string | null;
    } = {}
  ): ZTEntry => {
    const e: ZTEntry = {
      id: `zt${seq++}`,
      kind,
      status: init.status ?? "in_progress",
      name: init.name ?? null,
      family: init.family ?? "other",
      parentAgent,
      startedAt: 0,
      endedAt: null,
      events: 0,
      groupCount: 0,
      args: init.args ?? null,
      detail: null,
      error: null,
      blocked: false,
      hooks: [],
      thought: init.thought ?? null,
      durationMs: 0,
      files: [],
      tokens: null,
      windowTokens: null,
    };
    entries.push(e);
    byId.set(e.id, e);
    insertion.push(e);
    return e;
  };

  // live state
  let currentAgent: string | null = null;
  const agentEntry = new Map<string, ZTEntry>();
  const openCalls = new Map<string, ZTEntry>(); // tool name (universe does not emit ids) → row
  let openReasoning: ZTEntry | null = null;
  let thoughtOpenTs: number | null = null;
  let rounds = 0;

  const panel: ZTPanel = {
    running: false,
    outcome: null,
    goal: opts.taskText ?? null,
    plans: [],
    agentsRunning: [],
    agentsEnded: 0,
    workflowsEnded: 0,
    todo: null,
    changesFiles: 0,
    changesTools: 0,
    terminals: 0,
    branch: null,
    clean: true,
    environmentTools: false,
    contextUsage: { used: null, total: null, estimate: false },
    providerHalt: null,
    startedAt: null,
    endedAt: null,
    toolCount: 0,
  };
  const filesSet = new Set<string>();
  const gitTools = new Set<string>();
  let terminalCalls = 0;
  const runningAgentNames = new Map<string, number>();

  const ensureAgent = (name: string, ts: number): ZTEntry => {
    let a = agentEntry.get(name);
    if (!a) {
      a = push("agent", null, { name, family: "other" });
      a.startedAt = ts;
      a.groupCount = 1;
      agentEntry.set(name, a);
    }
    return a;
  };

  /** close the open thinking span at the exact instant visible work begins */
  const closeThought = (ts: number, final: ZTStatus = "completed") => {
    if (thoughtOpenTs !== null && openReasoning) {
      openReasoning.durationMs += Math.max(0, (ts - thoughtOpenTs) * 1000);
    }
    thoughtOpenTs = null;
    if (openReasoning) {
      openReasoning.endedAt = ts;
      if (openReasoning.status === "in_progress") openReasoning.status = final;
      openReasoning = null;
    }
  };

  /** the LAST tool row still in_progress with this exact name — binds the
   *  requested/running/terminal events of ONE call onto ONE row (a
   *  sequential repeat of the same tool finds no in_progress row and gets
   *  a fresh row; a parallel sibling folds into the open one) */
  const lastOpenToolRow = (tool: string): ZTEntry | undefined => {
    if (openCalls.has(tool)) return openCalls.get(tool);
    for (let i = insertion.length - 1; i >= 0; i--) {
      const r = insertion[i];
      if (r.kind !== "tool") continue;
      if ((r.name ?? "").toLowerCase() !== tool.toLowerCase()) continue;
      return r.status === "in_progress" ? r : undefined;
    }
    return undefined;
  };

  /** a same-named tool row that ended moments ago (same batch — terminal
   *  events can repeat for parallel calls of one tool name) */
  const justEndedToolRow = (tool: string, ts: number): ZTEntry | undefined => {
    for (let i = insertion.length - 1; i >= 0; i--) {
      const r = insertion[i];
      if (r.kind !== "tool") continue;
      if ((r.name ?? "").toLowerCase() !== tool.toLowerCase()) continue;
      if (r.endedAt != null && Math.abs(ts - r.endedAt) < 2) return r;
      return undefined;
    }
    return undefined;
  };

  /** fold a tool event into its own row — no family merging: each tool owns its icon+name */
  const rowFor = (tool: string, ts: number, owner: string | null): ZTEntry => {
    void ts;
    const fam = toolFamily(tool);
    const open = openCalls.get(tool);
    if (open) return open;
    const e = push("tool", owner, { name: tool, family: fam });
    e.startedAt = ts;
    e.groupCount = 1;
    if (fam === "terminal") {
      panel.terminals += 1;
      terminalCalls += 1;
    }
    if (fam === "changes") panel.changesTools += 1;
    if (tool.startsWith("git") || tool.startsWith("Git")) {
      gitTools.add(tool);
      panel.environmentTools = true;
    }
    return e;
  };

  for (const ev of events) {
    const ts = ev.ts;
    switch (ev.type) {
      case "turn_started":
      case "turn_started": {
        // legacy mirrors
        panel.startedAt = ts;
        panel.running = true;
        break;
      }
      case "turn_planned":
      case "turn_replanned": {
        // legacy mirrors (the universe_plan era)
        if (ev.plan?.length) {
          // the current plan doc: a replanned run replaces the whole list
          panel.plans = ev.plan.map(s => ({
            step: Number(s?.step ?? 0),
            agent: String(s?.agent ?? "universe"),
            instruction: String(s?.instruction ?? ""),
          }));
          if (!panel.goal) panel.goal = panel.plans[0]?.instruction ?? null;
        }
        closeThought(ts);
        break;
      }
      case "llm_round": {
        rounds = Math.max(rounds, (ev.round_index ?? rounds) + 1);
        if (!openReasoning) {
          openReasoning = push("reasoning", null, { family: "other" });
          openReasoning.startedAt = ts;
          openReasoning.groupCount = 1;
        }
        thoughtOpenTs ??= ts;
        break;
      }
      case "llm_delta": {
        if (!openReasoning) {
          openReasoning = push("reasoning", null, { family: "other" });
          openReasoning.startedAt = ts;
          openReasoning.groupCount = 1;
        }
        thoughtOpenTs ??= ts;
        if (typeof ev.text === "string" && ev.text) {
          const cur = openReasoning.thought ?? "";
          openReasoning.thought = (cur + ev.text).slice(-THOUGHT_CAP);
        }
        break;
      }
      case "tool_requested":
      case "tool_running": {
        if (!ev.tool) break;
        closeThought(ts);
        // Universe starts the flow flat: the MAIN agent row ("universe" today,
        // legacy "general") is never rendered (deleted per user annotation
        // 2026-10-30), so its tool calls render TOP-LEVEL; only a SUBAGENT's
        // events nest under their branch row
        // MAIN_AGENT hoisted to module scope
        const owner = ev.agent
          ? MAIN_AGENT.has(ev.agent)
            ? null
            : ev.agent
          : currentAgent && !MAIN_AGENT.has(currentAgent)
            ? currentAgent
            : null;
        // ONE row per real tool call: tool_requested already opened the row,
        // so the immediately-following tool_running must fold into it —
        // opening a second row here is what produced the phantom
        // "interrupted" duplicate for every call (live fix 2026-11-01).
        const openRow = lastOpenToolRow(ev.tool);
        const row = openRow ?? rowFor(ev.tool, ts, owner);
        row.events += 1;
        row.startedAt ||= ts;
        if (ev.args_summary) row.args = ev.args_summary;
        openCalls.set(ev.tool, row);
        if (ev.type === "tool_running") {
          panel.toolCount += 1;
        }
        break;
      }
      case "tool_finished": {
        if (!ev.tool) break;
        let row = lastOpenToolRow(ev.tool);
        // History replay: tool_finished without a prior tool_running (synthetic
        // events from persisted tool_history) — create the row standalone
        if (!row) {
          // a second terminal event for a parallel sibling of the same tool
          // name lands right after the first: fold it into that recent row
          // instead of minting a duplicate (parallel-batch guard)
          const sibling = justEndedToolRow(ev.tool, ts);
          if (sibling) {
            sibling.events += 1;
            if (typeof ev.duration_ms === "number")
              sibling.durationMs += ev.duration_ms;
            if (typeof ev.detail === "string" && !sibling.detail)
              sibling.detail = ev.detail;
            closeThought(ts);
            break;
          }
          const owner = ev.agent
            ? MAIN_AGENT.has(ev.agent)
              ? null
              : ev.agent
            : currentAgent && !MAIN_AGENT.has(currentAgent)
              ? currentAgent
              : null;
          row = rowFor(ev.tool, ts, owner);
          row.events += 1;
          row.status = "completed";
          row.endedAt = ts;
          if (typeof ev.detail === "string") row.detail = ev.detail;
          else if (
            typeof (ev as unknown as { args_summary?: string }).args_summary ===
            "string"
          )
            row.detail = (
              ev as unknown as { args_summary?: string }
            ).args_summary!;
          else if (
            typeof (ev as unknown as { result_preview?: string })
              .result_preview === "string"
          )
            row.detail = (
              ev as unknown as { result_preview?: string }
            ).result_preview!;
          else if (
            typeof (ev as unknown as { args?: unknown }).args === "object" &&
            (ev as unknown as { args?: unknown }).args
          ) {
            try {
              row.args = JSON.stringify(
                (ev as unknown as { args?: unknown }).args
              ).slice(0, 200);
            } catch {}
          }
          if (typeof ev.duration_ms === "number")
            row.durationMs += ev.duration_ms;
          // Count toward panel stats (rowFor already did for fresh rows, but
          // for history replay we need to ensure panel reflects it)
          panel.toolCount += 1;
          closeThought(ts);
          break;
        }
        row.events += 1;
        row.detail =
          ev.detail ??
          (ev as unknown as { result_preview?: string }).result_preview ??
          ev.args_summary ??
          row.detail;
        if (
          typeof (ev as unknown as { args?: unknown }).args === "object" &&
          (ev as unknown as { args?: unknown }).args &&
          !row.args
        ) {
          try {
            row.args = JSON.stringify(
              (ev as unknown as { args?: unknown }).args
            ).slice(0, 200);
          } catch {}
        }
        if (typeof ev.duration_ms === "number")
          row.durationMs += ev.duration_ms;
        row.blocked = false;
        openCalls.delete(ev.tool);
        const remaining = Array.from(openCalls.entries()).filter(
          ([, r]) => r === row
        ).length;
        if (remaining === 0) {
          row.status = "completed";
          row.endedAt = ts;
        }
        closeThought(ts);
        break;
      }
      case "tool_failed": {
        if (!ev.tool) break;
        let row = lastOpenToolRow(ev.tool);
        if (!row) {
          const sibling = justEndedToolRow(ev.tool, ts);
          if (sibling) {
            sibling.events += 1;
            if (sibling.status === "completed") sibling.status = "failed";
            sibling.error =
              (ev.error as string) ?? sibling.error ?? "failed";
            sibling.endedAt = ts;
            break;
          }
          const owner = ev.agent
            ? MAIN_AGENT.has(ev.agent)
              ? null
              : ev.agent
            : currentAgent && !MAIN_AGENT.has(currentAgent)
              ? currentAgent
              : null;
          row = rowFor(ev.tool, ts, owner);
          row.events += 1;
          row.status = "failed";
          row.error =
            (ev.error as string) ??
            (ev as unknown as { result_preview?: string }).result_preview ??
            "failed";
          row.endedAt = ts;
          if (
            typeof (ev as unknown as { args?: unknown }).args === "object" &&
            (ev as unknown as { args?: unknown }).args &&
            !row.args
          ) {
            try {
              row.args = JSON.stringify(
                (ev as unknown as { args?: unknown }).args
              ).slice(0, 200);
            } catch {}
          }
          panel.toolCount += 1;
          break;
        }
        row.events += 1;
        row.status = "failed";
        row.error =
          ev.error ??
          (ev as unknown as { result_preview?: string }).result_preview ??
          "failed";
        row.blocked = false;
        openCalls.delete(ev.tool);
        const remaining = Array.from(openCalls.entries()).filter(
          ([, r]) => r === row
        ).length;
        if (remaining === 0) row.endedAt = ts;
        break;
      }
      case "tool_blocked": {
        if (!ev.tool) break;
        const blockedRow = openCalls.get(ev.tool);
        if (blockedRow) {
          blockedRow.events += 1;
          blockedRow.blocked = true;
          // CLOSE the call like every terminal event: the fact-picker retry
          // that follows the gate mints its OWN row (live fix 2026-11-01) —
          // keeping the key open folded a completed retry into the blocked row
          openCalls.delete(ev.tool);
        }
        break;
      }
      case "hook_intercepted": {
        if (!ev.tool) break;
        const row = openCalls.get(ev.tool);
        const label = `${ev.hook_id ?? "hook"} · ${ev.action ?? "block"}${
          ev.message ? ` — ${ev.message.slice(0, 120)}` : ""
        }`;
        if (row) row.hooks.push(label);
        break;
      }
      case "agent_started": {
        if (ev.agent) {
          closeThought(ts);
          ensureAgent(ev.agent, ts);
          currentAgent = ev.agent;
          if (!runningAgentNames.has(ev.agent)) {
            runningAgentNames.set(ev.agent, 1);
            panel.agentsRunning.push(ev.agent);
          }
        }
        break;
      }
      case "agent_complete": {
        if (ev.agent) {
          const a = ensureAgent(ev.agent, ts);
          a.status = ev.success === false ? "failed" : "completed";
          a.endedAt = ts;
          a.detail = ev.detail ?? ev.what_was_done ?? a.detail;
          runningAgentNames.delete(ev.agent);
          panel.agentsRunning = Array.from(runningAgentNames.keys());
          panel.agentsEnded += 1;
          if (currentAgent === ev.agent) currentAgent = null;
        }
        break;
      }
      case "subagent_spawned": {
        if (ev.agent) {
          closeThought(ts);
          const a = ensureAgent(ev.agent, ts);
          a.status = "in_progress";
          a.args = typeof ev.brief === "string" ? ev.brief.slice(0, 240) : null;
          a.parentAgent = typeof ev.parent === "string" ? ev.parent : null;
          runningAgentNames.set(ev.agent, 1);
          if (!panel.agentsRunning.includes(ev.agent)) {
            panel.agentsRunning.push(ev.agent);
          }
          currentAgent = ev.agent;
        }
        break;
      }
      case "subagent_complete": {
        if (ev.agent) {
          const a = ensureAgent(ev.agent, ts);
          a.status = ev.success === false ? "failed" : "completed";
          a.endedAt = ts;
          a.detail = typeof ev.summary === "string" ? ev.summary : a.detail;
          runningAgentNames.delete(ev.agent);
          panel.agentsRunning = Array.from(runningAgentNames.keys());
          panel.agentsEnded += 1;
          if (currentAgent === ev.agent) currentAgent = null;
        }
        break;
      }
      case "handoff": {
        if (ev.to_agent) {
          closeThought(ts);
          ensureAgent(ev.to_agent, ts);
          currentAgent = ev.to_agent;
          if (!runningAgentNames.has(ev.to_agent)) {
            runningAgentNames.set(ev.to_agent, 1);
            panel.agentsRunning.push(ev.to_agent);
          }
        }
        break;
      }
      case "skill_activated": {
        // Universe renders skill picks inside thinking details; attach to the
        // open row instead of inventing a new one
        const name = ev.skill_name ?? ev.skill;
        if (name && openReasoning) {
          openReasoning.hooks.push(`skill · ${name}`);
        }
        break;
      }
      case "context_usage": {
        panel.contextUsage = {
          used: typeof ev.used_tokens === "number" ? ev.used_tokens : null,
          total: typeof ev.window_tokens === "number" ? ev.window_tokens : null,
          estimate: ev.window_is_estimate === true,
        };
        break;
      }
      /* defensive: Universe's core does not emit compaction yet — if the
       * wire gains the type the timeline renders the Universe separator */
      case "context_compaction": {
        closeThought(ts);
        const e = push("compaction", null, { family: "other" });
        e.startedAt = ts;
        e.tokens = typeof ev.used_tokens === "number" ? ev.used_tokens : null;
        e.windowTokens =
          typeof ev.window_tokens === "number" ? ev.window_tokens : null;
        e.events += 1;
        e.groupCount = 1;
        e.status = ev.success === false ? "failed" : "completed";
        e.endedAt = ts;
        break;
      }
      case "todo_updated": {
        if (Array.isArray(ev.todos)) {
          panel.todo = {
            items: ev.todos.map(t => ({
              content: String(t.content ?? ""),
              status: String(t.status ?? "pending"),
            })),
            idx:
              typeof ev.in_progress_idx === "number"
                ? ev.in_progress_idx
                : null,
          };
          // Universe Plans section = the live TodoWrite list: a committed plan
          // IS the todo list (the turn_planned universe_plan block is
          // retired); only a multi-item list renders as a plan doc
          if (panel.todo.items.length > 1) {
            panel.plans = panel.todo.items.map((t, i) => ({
              step: i,
              agent: "universe",
              instruction: t.content,
            }));
          }
        }
        break;
      }
      case "artifact_created": {
        const path = typeof ev.path === "string" ? ev.path : "";
        if (!path) break;
        if (!filesSet.has(path)) {
          filesSet.add(path);
          panel.changesFiles += 1;
          // attach the file to the current family changes row if open
          for (let i = insertion.length - 1; i >= 0; i--) {
            const row = insertion[i];
            if (row.kind === "tool" && row.family === "changes") {
              row.files.push(path);
              break;
            }
          }
        }
        break;
      }
      case "automation_failed":
      case "automation_stalled": {
        panel.workflowsEnded += 1;
        break;
      }
      case "provider_halt": {
        panel.providerHalt = ev.reason ?? ev.error ?? "provider unavailable";
        break;
      }
      case "turn_failed":
      case "turn_failed": {
        // legacy mirrors
        closeThought(ts, "interrupted");
        for (const e of entries) {
          if (e.status === "in_progress" && e.kind === "tool") {
            e.status = "failed";
            e.endedAt = ts;
          }
        }
        panel.outcome = "failed";
        panel.endedAt = ts;
        panel.running = false;
        break;
      }
      case "turn_cancelled":
      case "turn_cancelled": {
        // legacy mirrors
        closeThought(ts, "interrupted");
        for (const e of entries) {
          if (e.status === "in_progress") {
            e.status = "interrupted";
            e.endedAt = ts;
          }
        }
        panel.outcome = "cancelled";
        panel.endedAt = ts;
        panel.running = false;
        break;
      }
      case "turn_completed":
      case "turn_completed": {
        // legacy mirrors
        closeThought(ts);
        for (const e of entries) {
          if (e.status === "in_progress") {
            e.status = "interrupted";
            e.endedAt = ts;
          }
        }
        if (openReasoning) openReasoning = null;
        panel.outcome = "completed";
        panel.endedAt = ts;
        panel.running = false;
        break;
      }
      default:
        break;
    }
  }

  // fold-time residuals: events may stop before an end marker (log tailing)
  if (openReasoning && thoughtOpenTs !== null) {
    openReasoning.durationMs += (Date.now() / 1000 - thoughtOpenTs) * 1000;
  }
  panel.clean = panel.changesFiles === 0 && filesSet.size === 0;

  // ---- the one live status line (the trailing universe contract) ----
  let status: ZTShimStatus;
  if (panel.providerHalt) {
    status = { kind: "error", agent: null, text: panel.providerHalt };
  } else if (consentsOpen > 0) {
    status = {
      kind: "consent",
      agent: currentAgent ?? null,
      text: "needs your approval",
    };
  } else if (panel.outcome === "completed") {
    status = { kind: "done", agent: null, text: "done" };
  } else if (panel.outcome === "failed") {
    status = { kind: "error", agent: null, text: "did not complete" };
  } else if (panel.outcome === "cancelled") {
    status = { kind: "stopped", agent: null, text: "stopped" };
  } else {
    let found: ZTShimStatus | null = null;
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type === "tool_running" && e.tool) {
        const owner =
          e.agent && e.agent !== "universe" && e.agent !== "general"
            ? e.agent
            : currentAgent;
        found = {
          kind: "working",
          agent: owner ?? null,
          text: owner ? `${owner} — ${e.tool}` : e.tool,
        };
        break;
      }
      if (e.type === "agent_started" && e.agent) {
        found = { kind: "working", agent: e.agent, text: e.agent };
        break;
      }
      if (e.type === "llm_round" || e.type === "llm_delta") {
        found = {
          kind: "thinking",
          agent: currentAgent ?? null,
          text: currentAgent ? `${currentAgent} — thinking` : "thinking",
        };
        break;
      }
      if (e.type === "consent_required") {
        found = {
          kind: "consent",
          agent: currentAgent ?? null,
          text: "needs your approval",
        };
        break;
      }
      if (
        e.type === "steer_received" ||
        e.type === "steer_queued" ||
        e.type === "turn_replanned"
      ) {
        found = {
          kind: "thinking",
          agent: null,
          text: "orchestrator re-planning…",
        };
        break;
      }
    }
    status = found ?? { kind: "thinking", agent: null, text: "thinking" };
  }

  void rounds;
  void terminalCalls;
  return { entries: insertion, panel, status, toolCount: panel.toolCount };
}
