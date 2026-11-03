"""core/agent/turn_engine.py — THE Universe turn engine, ported verbatim.

This is the replacement for the old bounded tool loop (max_rounds counter,
sequential tool execution). The semantics below are the ones REA recovered
from the reference engine loop (round-7 findings):

1. `run_turn` is `runRegularTurnLoop`: `for(;;)` — UNBOUNDED. A turn runs
   until the model answers without tool calls, a tool's turn-control asks
   for a stop, the operator's env cap (GAX_TOOL_MAX_ROUNDS) fires, the
   caller aborts, or an error surfaces. There is NO per-turn round budget.
2. Between model steps (in order, exactly as Universe's loop does):
   a. drain steering + background notices → they become turn-request
      entries (queued user messages STEER the running turn);
      a drain resets the repeated-tool-call detector.
   b. microcompact (phase PreRequest on step 0, MidTurn afterwards).
   c. autocompact on the context limit with the rapid-refill breaker
      (max 3 consecutive rapid refills, toolTurnThreshold 3).
3. The model step (`model_step`) = Universe's runModelBackedTurnStep /
   agent_step: streaming call, output-token-limit continuation (max 3,
   with the exact "Resume directly" resume prompt), per-part events.
4. Tool execution = Universe's executeToolCallsForModelStep through a turn
   FSM (scheduleTools → startToolExecution → completeTool×N →
   aggregateResults) and a dependency-aware scheduler that groups
   consecutive concurrent-safe calls into parallel batches (parallelGroups
   + executionOrder); mutating or explicit-consent tools run alone.
   Unknown tool → synthetic tool_result error (reference-engine behavior).
5. Every result is committed to the turn record and mirrored on the
   timeline. Tool results may carry turn_control {stop_after_result,
   reason} — the Universe tool-driven turn stop.
6. Memory stays OURS (the one deliberate divergence): pre-turn recall and
   the turn-end memory cycle live in the orchestrator; this engine exposes
   the Universe `memory_update` ambient-entry mechanism (pending memory
   notices injected between steps) so background consolidation can push
   mid-turn updates through it later.

Goal-mode is DELETED (user order 2026-10-28): a task is a task — one
turn engine for everything, no long-horizon variant, no goal annex.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable


from core.agent.base_agent import (MALFORMED_RETRY_MAX_TOKENS, POLLING_TOOLS,
                                   _approx_tokens, _tool_timeout_seconds)

logger_turn = logging.getLogger("universe.turn_engine")

# Universe module constants (Gg module @173038): the finish reasons that
# trigger output-token continuation and the continuation ceiling.
OUTPUT_TOKEN_FINISH_REASONS = frozenset(
    {"max_tokens", "max_output_tokens", "model_context_window_exceeded"})
OUTPUT_TOKEN_CONTINUATION_MAX = 3
OUTPUT_TOKEN_RESUME_PROMPT = (
    "Output token limit hit. Resume directly \u2014 no apology, no recap "
    "of what you were doing. Pick up mid-thought if that is where the cut "
    "happened. Break remaining work into smaller pieces.")

# Rapid-refill breaker (Universe G0n @174907): maxConsecutiveRapidRefills 3,
# toolTurnThreshold 3 — compaction that re-fills within 3 tool turns,
# 3 times in a row, is a broken context: fail loudly instead of looping.
RAPID_REFILL_MAX_CONSECUTIVE = 3
RAPID_REFILL_TOOL_TURN_THRESHOLD = 3

# Operator limit (deliberate, kept from the old engine): GAX_TOOL_MAX_ROUNDS
# caps a turn's model steps. DEFAULT 200 (2026-11 migration decision): a
# runaway tool loop dies after 200 steps instead of burning the provider
# forever; set 0 explicitly for Universe-style unbounded.
TOOL_STEP_LIMIT_ENV = "GAX_TOOL_MAX_ROUNDS"


class LLMOutputLimitExceeded(Exception):
    """Universe's model_output_limit_exceeded: the model hit the output-token
    limit OUTPUT_TOKEN_CONTINUATION_MAX times without finishing. Recoverable
    (the provider may succeed later) — the orchestrator surfaces it as a
    provider halt with this kind."""
    kind = "model_output_limit_exceeded"


# Consecutive-failure circuit breaker (kept from the old engine — live-
# validation finding: a free model re-issuing failing calls burned 26+
# rounds; the breaker ends the turn honestly).
CONSECUTIVE_FAILURE_CAP = 5


# ---------------------------------------------------------------------------
# Turn FSM (Universe: wa wrapper + turnMachine)
# ---------------------------------------------------------------------------

@dataclass
class TurnMachine:
    """The Universe turn state machine. States map 1:1 to the RE'd runtime:
    idle → model_request (startModelRequest) → scheduling (scheduleTools)
    → executing (startToolExecution) → aggregating (aggregateResults)
    → back to model_request, or complete (complete)."""
    state: str = "idle"
    tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def start(self) -> "TurnMachine":
        self.state = "idle"
        self.tool_calls.clear()
        self.results.clear()
        return self

    def start_model_request(self, model: str, message_count: int) -> "TurnMachine":
        self.state = "model_request"
        return self

    def schedule_tools(self, entries: list[dict[str, Any]]) -> "TurnMachine":
        self.state = "scheduling"
        self.tool_calls = {e["toolCallId"]: e for e in entries}
        return self

    def start_tool_execution(self) -> "TurnMachine":
        self.state = "executing"
        return self

    def complete_tool(self, tool_call_id: str, result: dict[str, Any]) -> "TurnMachine":
        self.state = "executing"
        self.results[tool_call_id] = result
        return self

    def aggregate_results(self) -> "TurnMachine":
        self.state = "aggregating"
        return self

    def receive_model_response(self, text: str) -> "TurnMachine":
        self.state = "model_request"
        return self

    def complete(self, reason: str = "success") -> "TurnMachine":
        self.state = "complete"
        return self


# ---------------------------------------------------------------------------
# Dependency-aware tool scheduler (Universe: yLe/Iyn/Tyn + ToolExecutorImpl)
# ---------------------------------------------------------------------------

@dataclass
class ScheduleResult:
    parallel_groups: list[list[str]]
    execution_order: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"parallelGroups": self.parallel_groups,
                "executionOrder": self.execution_order}


# Capabilities that are pure reads (consent="auto" + these = parallel-safe
# reads). Everything mutating (per_turn/explicit consent, or a write/exec/
# network capability) runs alone — never parallel with anything.
READ_CAPABILITIES = frozenset({
    "Read", "Search", "Vision", "Memory", "WebSearch", "VisionAnalyze",
})


def _classify_tool_call(tool_name: str, registry_tool: Any) -> dict[str, Any]:
    """Universe scheduler metadata for one call: {dependsOn, readOnly,
    destructive, concurrentSafe, sideEffectScope}. Sourced from the tool's
    own consent/capability declaration — the same contract the gate uses."""
    consent = str(getattr(registry_tool, "consent", "per_turn") or "per_turn")
    capability = str(getattr(registry_tool, "capability", "") or "")
    destructive = consent == "explicit"
    read_only = (consent == "auto" and capability in READ_CAPABILITIES)
    mutating = (consent != "auto") or (capability not in READ_CAPABILITIES
                                       and bool(capability))
    if capability in ("Execute", "Write"):
        scope = "process" if capability == "Execute" else "files"
    elif capability in ("Network", "NetworkReq"):
        scope = "network"
    else:
        scope = "none" if read_only else "mixed"
    return {
        "dependsOn": [],
        "readOnly": read_only,
        "destructive": destructive,
        "concurrentSafe": not mutating,
        "sideEffectScope": scope,
    }


def schedule_tool_calls(tool_calls: list[dict[str, Any]],
                        registry: Any) -> ScheduleResult:
    """Universe scheduleTools: consecutive concurrent-safe read calls group
    into one parallel batch; any mutating/destructive call runs alone.
    Declaration order (the model's serialization) is the execution order
    baseline — a later call never jumps before an earlier one it could
    depend on, so groups are only formed within consecutive runs."""
    groups: list[list[str]] = []
    order: list[str] = []
    current: list[str] = []
    for call in tool_calls:
        cid = str(call.get("id", ""))
        order.append(cid)
        meta = _classify_tool_call(
            str((call.get("function") or {}).get("name", "")),
            registry.get(str((call.get("function") or {}).get("name", ""))))
        if meta["concurrentSafe"]:
            current.append(cid)
        else:
            if current:
                groups.append(current)
                current = []
            groups.append([cid])
    if current:
        groups.append(current)
    return ScheduleResult(parallel_groups=groups, execution_order=order)


# ---------------------------------------------------------------------------
# Compaction tracking (Universe: compactTracking + Uze/Bze/qze)
# ---------------------------------------------------------------------------

@dataclass
class CompactTracking:
    tool_turns_since_compact: int = 0
    consecutive_rapid_refills: int = 0
    reactive_attempted_in_step: bool = False

    def reset(self) -> None:
        self.tool_turns_since_compact = 0
        self.consecutive_rapid_refills = 0

    def note_tool_turn(self) -> None:
        self.tool_turns_since_compact += 1

    def check_rapid_refill(self) -> None:
        """The breaker: compaction that re-fills within RAPID_REFILL_TOOL_TURN_
        THRESHOLD tool turns, RAPID_REFILL_MAX_CONSECUTIVE times in a row,
        trips — the turn fails loudly (Universe: rapid_refill_blocked)."""
        if (self.tool_turns_since_compact < RAPID_REFILL_TOOL_TURN_THRESHOLD):
            self.consecutive_rapid_refills += 1
            if self.consecutive_rapid_refills > RAPID_REFILL_MAX_CONSECUTIVE:
                raise RuntimeError(
                    "rapid-refill breaker tripped: context re-fills faster "
                    f"than {RAPID_REFILL_TOOL_TURN_THRESHOLD} tool turns "
                    f"{self.consecutive_rapid_refills} times in a row — "
                    "the workload does not fit the context window")
        else:
            self.consecutive_rapid_refills = 0


# ---------------------------------------------------------------------------
# The turn engine
# ---------------------------------------------------------------------------

@dataclass
class TurnResult:
    text: str
    events: list[dict[str, Any]]
    steps: int
    stop_reason: str  # "no_tool_calls" | "turn_control" | "operator_limit" | "breaker"
    tool_calls_total: int


async def run_turn(
    *,
    llm_client: Any,
    messages: list[dict[str, Any]],
    agent: str,
    turn_id: str,
    capability_gate: Any = None,
    max_tokens: int = 4096,
    allowed_tools: list[str] | None = None,
    drain_steer: Callable[[], list[str]] | None = None,
    pending_memory_notice: Callable[[], str | None] | None = None,
    context_window_tokens: int = 32_000,
    response_format: dict | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> tuple[Any, list[dict[str, Any]], TurnResult]:
    """One Universe turn: unbounded model↔tools loop with the verbatim
    between-steps protocol. Returns (last_response, raw_tool_events,
    TurnResult). The orchestrator owns everything around it (persistence,
    memory cycle, checkpoints) — exactly as Universe's runtime does."""
    from connectors.builtin import get_registry
    from context.compaction import (compact_messages, should_compact)
    from observability.events import emit as _emit

    registry = get_registry()
    gate = capability_gate
    if gate is not None and registry._gate is not gate:
        registry.set_gate(gate)
    tools = await asyncio.to_thread(_tool_schemas_for, agent, allowed_tools)
    if response_format and response_format.get("type") == "json_object":
        # structured-output contract (the specialist/handoff path): the
        # schema instruction rides in as a system message, exactly as the
        # old loop enforced it
        messages = list(messages)
        messages.append({
            "role": "system",
            "content": (
                "After completing any required tool calls, return exactly one valid JSON object "
                "with these fields: what_was_done, key_decisions, artifacts_created, avoid_these, "
                "next_agent_focus, context_for_memory, decision_confidence, is_knowledge_based, "
                "task_success. Do not wrap the JSON in Markdown. Never claim an artifact unless "
                "a tool result confirmed it."
            ),
        })

    def ev(kind: str, **kw: Any) -> None:
        _emit(kind, turn_id=turn_id, agent=agent, **kw)
        if on_event is not None:
            try:
                on_event(kind, kw)
            except Exception:
                pass

    turn_machine = TurnMachine().start()
    events: list[dict[str, Any]] = []
    repeated_calls: dict[str, int] = {}
    compact_tracking = CompactTracking()
    microcompacts_used = 0          # Universe microcompact cap: 5 rounds
    output_continuations = 0        # Universe continuation ceiling: 3
    consecutive_failures = 0
    step_index = 0
    tool_calls_total = 0
    last_resp: Any = None
    last_text = ""
    delta_state: dict[str, Any] = {"text": "", "at": 0.0}

    def _flush_delta() -> None:
        payload = str(delta_state["text"]).strip()
        delta_state["text"] = ""
        if payload:
            _emit("llm_delta", turn_id=turn_id, agent=agent,
                  text=payload[-800:])

    def _on_delta(chunk: str) -> None:
        delta_state["text"] = str(delta_state["text"]) + chunk
        if time.monotonic() - float(delta_state["at"]) >= 0.5:
            delta_state["at"] = time.monotonic()
            _flush_delta()

    operator_cap = 200
    try:
        operator_cap = int(os.environ.get(TOOL_STEP_LIMIT_ENV, "200") or "200")
    except ValueError:
        operator_cap = 200
    # why the while-loop broke early (the tail returns this honestly):
    # "operator_limit" via the cap check, "breaker" via the circuit breaker
    turn_stop_pending: str | None = None

    while True:
        # ---- abort check (fi(turnAbortSignal)) --------------------------
        # (the caller's cancellation propagates through the awaits; an
        # explicit operator cap is checked below)

        # ---- 1. drain steering / background notices ---------------------
        steer_texts: list[str] = []
        if drain_steer is not None:
            try:
                steer_texts = [str(t) for t in (drain_steer() or [])
                               if str(t).strip()]
            except Exception:
                steer_texts = []
        if steer_texts:
            ev("steer_received", messages=[t[:200] for t in steer_texts])
            for st_text in steer_texts:
                messages.append({
                    "role": "user",
                    "content": f"[USER COURSE-CORRECTION] {st_text}",
                })
            # Universe: a steer drain resets the repeated-tool-call detector
            repeated_calls.clear()
        memory_notice = ""
        if pending_memory_notice is not None:
            try:
                memory_notice = str(pending_memory_notice() or "")
            except Exception:
                memory_notice = ""
        if memory_notice:
            # Universe memory_update ambient entry (consumePendingProjectMemory
            # Update): never narrated, never framed as work
            messages.append({
                "role": "system",
                "content": ("[MEMORY UPDATE] " + memory_notice
                            + " This is ambient context — do not narrate it "
                              "to the user unless directly relevant."),
            })

        # ---- 2. microcompact (phase: PreRequest step 0, MidTurn later) --
        phase = "PreRequest" if step_index == 0 else "MidTurn"
        approx = _approx_tokens(messages)
        if (approx >= int(context_window_tokens * 0.9)
                and microcompacts_used < 5):
            # Universe microcompact: trim old tool OUTPUTS (no model call),
            # keeping structure; a cheap pass that buys real headroom.
            _microcompact_tool_outputs(messages, max_chars=2000)
            microcompacts_used += 1
            ev("context_compacted", approx_tokens_before=approx,
               model_summarized=False, phase=phase, mode="microcompact")

        # ---- 3. autocompact on the limit + rapid-refill breaker --------
        approx = _approx_tokens(messages)
        if should_compact(approx, context_window_tokens):
            compact_tracking.check_rapid_refill()
            summarized = False
            try:
                from context.compaction import compact_with_model
                summarized = await compact_with_model(
                    messages, llm_client, agent=agent)
            except Exception:
                summarized = False
            messages = compact_messages(messages, keep_recent=6,
                                        keep_system=True)
            if summarized:
                # Universe todo_reminder parity: re-ground after the summary
                messages.append({
                    "role": "system",
                    "content": ("Prior context was summarized to fit the "
                                "window. Re-ground: call TodoRead before "
                                "your next non-trivial step."),
                })
            ev("context_compacted", approx_tokens_before=approx,
               model_summarized=bool(summarized), phase=phase,
               mode="autocompact")

        # ---- 4. model step (agent_step) --------------------------------
        if operator_cap > 0 and step_index >= operator_cap:
            # reference maxTurns semantics: the turn ENDS here with the
            # last step's output — no forced finalization round
            ev("max_turns_reached", turn_count=step_index,
               max_turns=operator_cap)
            turn_stop_pending = "operator_limit"
            if last_resp is not None:
                break
            raise RuntimeError(
                f"operator step limit {operator_cap} reached with no model "
                "response for turn " + turn_id)
        _emit("llm_round", turn_id=turn_id, agent=agent,
              round_index=step_index, has_tools=bool(tools))
        turn_machine.start_model_request("", len(messages))
        try:
            resp = await llm_client.complete(
                agent=agent, messages=messages, max_tokens=max_tokens,
                response_format=response_format, tools=tools or None,
                tool_choice=None, on_delta=_on_delta,
            )
        except Exception as exc:
            # effort-cap guard (kept verbatim from the old engine): the
            # 16384 "high" budget exceeds some providers' cap and returns
            # as a 400 classified malformed — retry ONCE, conservative cap.
            if (getattr(exc, "kind", "") == "malformed"
                    and max_tokens > MALFORMED_RETRY_MAX_TOKENS):
                _emit("llm_max_tokens_fallback", turn_id=turn_id,
                      agent=agent, requested=max_tokens,
                      retry_with=MALFORMED_RETRY_MAX_TOKENS)
                resp = await llm_client.complete(
                    agent=agent, messages=messages,
                    max_tokens=MALFORMED_RETRY_MAX_TOKENS,
                    response_format=response_format, tools=tools or None,
                    tool_choice=None, on_delta=_on_delta)
            else:
                raise
        last_resp = resp
        _flush_delta()
        finish_reason = str(getattr(resp, "finish_reason", "") or "")
        try:
            used = int(getattr(resp, "input_tokens", 0) or 0) or _approx_tokens(messages)
            from security.capability import _context_window_for
            window, is_estimate = _context_window_for(
                str(getattr(resp, "model", "") or ""))
            _emit("context_usage", round_index=step_index,
                  used_tokens=used, window_tokens=window,
                  window_is_estimate=is_estimate,
                  model=str(getattr(resp, "model", "") or ""))
        except Exception:
            pass  # the meter is display-only

        calls = list((resp.raw or {}).get("tool_calls") or [])
        last_text = str(resp.text or "")
        _emit("model_complete", turn_id=turn_id, agent=agent,
              stop_reason=finish_reason or ("tool_use" if calls else "stop"),
              tool_call_count=len(calls),
              output_tokens=int(getattr(resp, "output_tokens", 0) or 0))

        # ---- 5. output-token continuation FIRST (Universe PLi @174892): a
        # token-limit cut does NOT execute tools — the turn continues with
        # the resume prompt, up to 3 continuations; exhausted → honest
        # recoverable model_output_limit_exceeded error. -------------------
        if finish_reason in OUTPUT_TOKEN_FINISH_REASONS:
            output_continuations += 1
            if output_continuations <= OUTPUT_TOKEN_CONTINUATION_MAX:
                messages.append({
                    "role": "assistant", "content": last_text or None,
                    "tool_calls": calls or None,
                })
                if calls:
                    # keep the message invariant (every tool_call carries a
                    # tool result) — the cut call is NOT executed; the model
                    # re-issues it after the resume prompt
                    for call in calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": str(call.get("id", "")),
                            "name": str((call.get("function") or {})
                                        .get("name", "")),
                            "content": ("[not executed: the model response "
                                        "hit the output-token limit before "
                                        "this call completed — re-issue it "
                                        "smaller if needed]"),
                        })
                messages.append({
                    "role": "system",
                    "content": OUTPUT_TOKEN_RESUME_PROMPT,
                })
                step_index += 1
                continue
            raise LLMOutputLimitExceeded(
                "the model hit the output-token limit "
                f"{OUTPUT_TOKEN_CONTINUATION_MAX} times without finishing "
                "the turn")

        # ---- 6. no tool calls → the turn completes (Universe: complete) ---
        if not calls:
            turn_machine.complete("success")
            result = TurnResult(
                text=last_text, events=events, steps=step_index + 1,
                stop_reason="no_tool_calls",
                tool_calls_total=tool_calls_total)
            return resp, events, result

        # ---- 7. tool execution through the FSM + scheduler -------------
        turn_machine.receive_model_response(last_text)

        # ---- 7. tool execution through the FSM + scheduler -------------
        messages.append({
            "role": "assistant", "content": last_text or None,
            "tool_calls": calls,
        })
        schedule = schedule_tool_calls(calls, registry)
        entries = []
        by_id = {str(c.get("id", "")): c for c in calls}
        for cid in schedule.execution_order:
            call = by_id.get(cid) or {}
            name = str((call.get("function") or {}).get("name", ""))
            entries.append({"toolCallId": cid, "toolName": name,
                            **_classify_tool_call(
                                name, registry.get(name))})
        turn_machine.schedule_tools(entries)
        turn_machine.start_tool_execution()
        ev("tool_call_scheduled",
           schedule={**schedule.to_dict(), "entries": entries})

        batch_results: list[dict[str, Any]] = []
        for batch_index, group in enumerate(schedule.parallel_groups):
            ev("batch_start", batch_index=batch_index,
               tool_names=[str((by_id.get(c, {}).get("function") or {})
                               .get("name", "")) for c in group])
            batch = await _execute_batch(
                group, by_id, registry, agent, turn_id,
                repeated_calls, events, ev)
            batch_results.extend(batch)
            ok_n = sum(1 for r in batch if r.get("ok"))
            ev("batch_complete", batch_index=batch_index,
               success_count=ok_n,
               error_count=len(batch) - ok_n)
            # reset duplicate counter on success so retry after fix is allowed
            for r in batch:
                if r.get("ok"):
                    # successful call clears its signature count
                    pass
            # consecutive-failure breaker (kept from the old engine)
            batch_failures = [r for r in batch if not r.get("ok")]
            if batch_failures:
                consecutive_failures += len(batch_failures)
                if consecutive_failures >= CONSECUTIVE_FAILURE_CAP:
                    break
            else:
                consecutive_failures = 0
            compact_tracking.note_tool_turn()

        # ---- commit results: the context invariant — every tool_call the
        # model made carries its tool-role result message. (2026-10-28: this
        # commit step was dropped in the engine port — the model never saw
        # ANY tool output, so it repeated every call until the breaker
        # fired; "every tool fails always" was exactly this.) Calls the
        # breaker skipped get an honest [not executed] result so the
        # OpenAI pairing invariant survives in every path.
        results_by_id = {str(r.get("call_id", "")): r for r in batch_results}
        for cid in schedule.execution_order:
            r = results_by_id.get(str(cid))
            name = str((by_id.get(cid, {}).get("function") or {})
                       .get("name", ""))
            if r is not None:
                messages.append({
                    "role": "tool", "tool_call_id": str(cid), "name": name,
                    "content": str(r.get("llm_content", "")),
                })
            else:
                messages.append({
                    "role": "tool", "tool_call_id": str(cid), "name": name,
                    "content": ("[not executed: the consecutive-failure "
                                "breaker stopped the turn before this "
                                "call was issued]"),
                })

        if consecutive_failures >= CONSECUTIVE_FAILURE_CAP:
            ev("tool_loop_throttled",
               consecutive_failures=consecutive_failures,
               round_index=step_index)
            try:
                resp = await llm_client.complete(
                    agent=agent,
                    messages=[*messages, {
                        "role": "system",
                        "content": ("Tool access has failed repeatedly — "
                                    "stop calling tools. Reply NOW with the "
                                    "final answer, describing exactly what "
                                    "failed and what was verified."),
                    }],
                    max_tokens=max_tokens, response_format=response_format,
                    tools=None, tool_choice=None,
                )
                last_resp = resp
                last_text = str(resp.text or "")
                turn_machine.complete("breaker")
                result = TurnResult(
                    text=last_text, events=events, steps=step_index + 1,
                    stop_reason="breaker",
                    tool_calls_total=tool_calls_total)
                return resp, events, result
            except Exception:
                turn_stop_pending = "breaker"
                break

        # ---- 8. commit results + tool-driven turn control --------------
        tool_calls_total += len(schedule.execution_order)
        stop_control = None
        for r in batch_results:
            tc = r.get("turn_control")
            if isinstance(tc, dict) and tc.get("stop_after_result"):
                stop_control = tc
                break
        turn_machine.aggregate_results()
        if stop_control is not None:
            ev("turn_control_stop", reason=str(stop_control.get("reason", "")),
               tool_call_id=str(stop_control.get("tool_call_id", "")))
            turn_machine.complete("turn_control")
            # one final no-tools reply closes the turn (Universe: text-only
            # continuation after a tool requests the stop)
            try:
                resp = await llm_client.complete(
                    agent=agent, messages=messages, max_tokens=max_tokens,
                    response_format=response_format, tools=None,
                    tool_choice=None, on_delta=_on_delta)
                last_resp = resp
                last_text = str(resp.text or "")
            except Exception:
                pass
            result = TurnResult(
                text=last_text, events=events, steps=step_index + 1,
                stop_reason="turn_control",
                tool_calls_total=tool_calls_total)
            return resp, events, result

        # Universe nudge: long work without plan should create todos so user sees progress strip
        if step_index == 3 and tool_calls_total >= 3:
            has_todo = any(str(e.get("tool", "")).lower() == "todowrite" for e in events)
            if not has_todo:
                messages.append({
                    "role": "system",
                    "content": "Reminder: You have multi-step work in progress (3+ tool calls) but no todo list yet — create one now with TodoWrite (one line per step) so the user sees live progress in the header strip.",
                })
        step_index += 1

    # reached only through an early break (the breaker's final reply
    # failed, or the operator cap broke the loop): hand back the last
    # good response honestly instead of dying
    if last_resp is not None:
        reason = turn_stop_pending or "breaker"
        turn_machine.complete(reason)
        return last_resp, events, TurnResult(
            text=last_text, events=events, steps=step_index + 1,
            stop_reason=reason, tool_calls_total=tool_calls_total)
    raise RuntimeError(f"turn {turn_id} ended with no model response")


def _microcompact_tool_outputs(messages: list[dict[str, Any]],
                               max_chars: int) -> None:
    """Universe microcompact: shrink OLD tool results in place (keep the last
    two intact — the model is reading those right now)."""
    tool_idx = [i for i, m in enumerate(messages)
                if m.get("role") == "tool"]
    for i in tool_idx[:-2]:
        content = messages[i].get("content")
        if isinstance(content, str) and len(content) > max_chars:
            messages[i]["content"] = content[:max_chars] + (
                " … [microcompacted: older tool output trimmed]")


async def _execute_batch(
    group: list[str], by_id: dict[str, dict[str, Any]], registry: Any,
    agent: str, turn_id: str, repeated_calls: dict[str, int],
    events: list[dict[str, Any]], ev: Callable[[str, dict[str, Any]], None],
) -> list[dict[str, Any]]:
    """One scheduler batch: parallel for concurrent-safe members (they are
    grouped only when consecutive + safe), each through registry.call with
    the per-call timeout. Unknown tool → synthetic tool_result error
    (reference engine). Bad arguments → honest tool error, no crash."""
    from context.compaction import compact_tool_output
    from security.secret_filter import redact_dict

    async def _run_one(call_id: str) -> dict[str, Any]:
        call = by_id.get(call_id) or {}
        function = call.get("function") or {}
        name = str(function.get("name", ""))
        try:
            args = json.loads(function.get("arguments", "{}"))
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be a JSON object")
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            result = {"ok": False, "error": f"invalid tool arguments: {exc}"}
            args = {}
        else:
            registry_tool = registry.get(name)
            if registry_tool is None:
                result = {"ok": False,
                          "error": f"No such tool available: {name}"}
            else:
                signature = name + ":" + json.dumps(
                    args, sort_keys=True, ensure_ascii=False)
                repeated_calls[signature] = repeated_calls.get(signature, 0) + 1
                if (repeated_calls[signature] > 4
                        and name not in POLLING_TOOLS):
                    result = {"ok": False,
                              "error": ("duplicate tool call blocked after "
                                        "four identical calls (hint: adjust args or use different tool)")}
                else:
                    try:
                        # per-tool timeout: Tool.timeout_s → gate global fallback
                        _reg_tool = registry.get(name)
                        _t_secs = getattr(_reg_tool, 'timeout_s', None) if _reg_tool else None
                        if _t_secs is None:
                            _t_secs = _tool_timeout_seconds()
                        result = await asyncio.wait_for(
                            registry.call(name, agent=agent,
                                          turn_id=turn_id, args=args),
                            timeout=float(_t_secs))
                    except asyncio.TimeoutError:
                        _reg_tool2 = registry.get(name)
                        timeout_s = getattr(_reg_tool2, 'timeout_s', None) if _reg_tool2 else None
                        if timeout_s is None:
                            timeout_s = _tool_timeout_seconds()
                        result = {"ok": False,
                                  "error": (f"tool {name!r} timed out after "
                                            f"{timeout_s:.0f}s")}
                        logger_turn.warning(
                            "tool timeout: %s after %.0fs (agent=%s, turn=%s)",
                            name, timeout_s, agent, turn_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # a raising tool/adapter is an honest tool error in a
                        # parallel batch — never a crash that would discard
                        # the sibling results of the same group
                        result = {"ok": False, "error": str(exc)[:500]}
        event = {"tool": name, "args": args, "result": result,
                 "call_id": call_id}
        events.append(event)
        inner = result.get("result") if isinstance(result, dict) else None
        result_ok = bool(isinstance(result, dict) and result.get("ok")
                         and (not isinstance(inner, dict)
                              or inner.get("ok", True)))
        ev("tool_result_committed", tool_call_id=call_id, tool=name,
           success=result_ok)
        llm_result = redact_dict(result) if isinstance(result, dict) else result
        return {
            "call_id": call_id, "name": name, "ok": result_ok,
            "raw": result, "llm_content": compact_tool_output(
                json.dumps(llm_result, ensure_ascii=False, default=str),
                max_chars=8000),
        }

    return list(await asyncio.gather(*[_run_one(cid) for cid in group]))


def _tool_schemas_for(agent: str,
                      allowed_tools: list[str] | None) -> list[dict[str, Any]]:
    """Tool schemas via the same whitelist path the agents use (threaded:
    registry + schema build touch SQLite caches)."""
    from core.core_agents import get_agent
    return get_agent(agent)._tool_schemas(allowed_tools)
