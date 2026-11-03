"""core/engine/session_loop.py — the session-owned turn loop.

The session is the unit of work (the Claude Code / Codex / ZCode model):

* every user message is a TURN inside a SESSION — no classification, no
  goal rows, no planets, no checkpoints;
* the model declares completion: the turn ends when the response carries
  no tool calls (enforced by core.agent.turn_engine) under a hard
  operator budget (GAX_TOOL_MAX_ROUNDS, default 200);
* the ONLY durable state is the append-only transcript
  (~/.universe/sessions/<id>/transcript.jsonl) — resume is a replay,
  fork is a copy, compaction is a recorded event;
* memory writes are NOT on the hot path: post-session reflection
  distills the transcript afterwards (see cli/repl.py startup).

The protocol between steps (steering drain, microcompact, autocompact,
claim scheduling, breakers) stays in core.agent.turn_engine — this loop
owns context assembly (full transcript replay, no 24x4000 truncation),
append-only persistence, and the session-shaped summary envelope.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable


def _new_turn_id() -> str:
    import uuid
    return "turn-" + uuid.uuid4().hex[:12]


# The session engine's context window matches the turn engine's default
# (run_turn's context_window_tokens=32_000): both compaction layers trip
# on the same budget.
SESSION_CONTEXT_WINDOW_TOKENS = 32_000


async def maybe_compact(*, session_id: str, llm_client: Any,
                        model: str = "") -> bool:
    """P3 — the ONE compute-based compaction (85% of the window).

    Runs over the transcript replay before a turn: when the replayed
    history approaches the model window, the older middle is summarized
    with the model (the CC /compact template — section 6 keeps every user
    message verbatim) and recorded as a synthetic compaction marker in
    the transcript. Replay then carries the summary, not the old rows —
    compaction is a LOGGED transcript event, exactly the Codex semantics.
    Falls back to the keyword heuristic (user messages preserved verbatim
    in the marker) when the model summary fails; returns False when
    nothing compacted."""
    from context.compaction import (compact_messages, compact_with_model,
                                    context_window_for_model,
                                    should_compact)
    from core.agent.base_agent import _approx_tokens
    from core.session.store import get_session_store
    store = get_session_store()
    msgs = store.history_messages(session_id)
    if len(msgs) < 8:
        return False
    window = (context_window_for_model(model) if model
              else SESSION_CONTEXT_WINDOW_TOKENS)
    pre = _approx_tokens(msgs)
    if not should_compact(pre, window):
        return False
    compacted = await compact_with_model(msgs, llm_client, agent="universe")
    if not compacted:
        # honest heuristic fallback — the transcript still gets ONE summary
        msgs[:] = compact_messages(msgs)
    summary = next(
        (str(m.get("content") or "") for m in reversed(msgs)
         if m.get("role") == "system"
         and str(m.get("content") or "").startswith(
             ("Prior context summary", "Compacted prior context",
              "[prior context compacted]"))),
        "")
    if not summary:
        return False
    if not compacted:
        # the heuristic summary drops user turns from its text — keep every
        # user message verbatim in the marker so constraints outlive it
        keep = [str(m.get("content") or "") for m in msgs
                if m.get("role") == "user"]
        if keep:
            summary += "\n\nPreserved verbatim (user messages):\n" + "\n".join(
                f"- {k}" for k in keep)[-12000:]
    store.compact_separator(session_id, pre, _approx_tokens(msgs),
                            summary[:16000])
    try:
        from observability.events import emit
        emit("context_compaction", session_id=session_id,
             pre_tokens=pre, post_tokens=_approx_tokens(msgs),
             model_summarized=compacted)
    except Exception:
        pass
    return True


def _summarize_tool_events(events: list[dict[str, Any]],
                           started_at: float) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fold raw engine tool events into (tools_used, tool_history, tool_failures)."""
    tools_used: set[str] = set()
    tool_history: list[dict[str, Any]] = []
    tool_failures: list[dict[str, Any]] = []
    for _ev in events or []:
        _tool = str(_ev.get("tool", ""))
        if not _tool:
            continue
        tools_used.add(_tool)
        try:
            _args = _ev.get("args") or {}
            _res = _ev.get("result") if isinstance(_ev.get("result"), dict) else {}
            _ok = bool(_res.get("ok"))
            _inner = _res.get("result")
            if isinstance(_inner, dict) and "ok" in _inner:
                _ok = bool(_inner.get("ok"))
            if isinstance(_res.get("result"), str):
                _preview = str(_res["result"])[:500]
            elif isinstance(_res.get("error"), str):
                _preview = str(_res["error"])[:300]
            else:
                _preview = json.dumps(_res, ensure_ascii=False)[:500]
            _ap = {k: (str(v)[:200] if isinstance(v, str) else v)
                   for k, v in (_args if isinstance(_args, dict) else {}).items()}
            _dur = _ev.get("duration_ms", _ev.get("elapsed_ms",
                       _ev.get("duration", 0)))
            try:
                _dur = int(_dur or 0)
            except Exception:
                _dur = 0
            tool_history.append({
                "tool": _tool, "ok": _ok, "args": _ap,
                "result_preview": _preview, "seq": _ev.get("seq", 0),
                "agent": str(_ev.get("agent") or _ev.get("owner_agent")
                             or "universe"),
                "duration_ms": _dur,
                "ts": _ev.get("ts", _ev.get("started_at", started_at))})
            if not _ok:
                tool_failures.append({"tool": _tool,
                                      "error": str(_res.get("error", ""))[:200]})
        except Exception:
            continue
    return sorted(tools_used), tool_history, tool_failures


async def run_session_turn(
    *,
    text: str,
    session_id: str,
    llm_client: Any,
    build_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    gate: Any = None,
    turn_id: str | None = None,
    language: str = "en",
    effort: str = "",
    on_step: Any = None,
    on_event: Any = None,
) -> dict[str, Any]:
    """One turn inside a session. Returns the same summary envelope the
    goal engine produced (so CLI/bridge keep working) MINUS the goal-era
    keys (gravity_score/gravity_bucket/promoted_to_l3) and PLUS
    session_id/engine."""
    from core.session.store import get_session_store
    from core.agent.steer import take_steer
    from observability.timeline import record as timeline_record

    store = get_session_store()
    turn_id = turn_id or _new_turn_id()
    started_at = time.time()

    if not store.get(session_id):
        try:
            store.create(id=session_id, workspace_root="")
        except Exception:
            pass

    # ---- the m3 event spine opens with a metadata row (no role) -------
    store.append(session_id, {"kind": "turn_start", "turn_id": turn_id,
                              "status": "executing",
                              "engine": "session"})

    async def _maybe_step(payload: dict[str, Any]) -> None:
        if on_step is None:
            return
        import inspect
        if inspect.iscoroutinefunction(on_step):
            await on_step(payload)
        else:
            try:
                on_step(payload)
            except Exception:
                pass

    await _maybe_step({"phase": "agent_turn", "agent": "universe",
                       "step": 0, "instruction": str(text)[:200]})
    timeline_record(turn_id, agent="universe", event="turn_start",
                    detail=str(text)[:200])

    # ---- append-only: the user message is the canonical replay row -----
    user_seq = store.append(session_id, {
        "kind": "message", "role": "user", "content": str(text),
        "turn_id": turn_id, "engine": "session"})

    # ---- P3: the ONE compaction pass (85% of the window) runs over the
    # replay BEFORE context assembly — a logged transcript event, never a
    # turn blocker.
    try:
        await maybe_compact(session_id=session_id, llm_client=llm_client)
    except Exception:
        pass

    # ---- context assembly: full transcript replay (no 24x4000 cut) ----
    history = store.history_messages(session_id, exclude_seq=user_seq)
    messages = build_messages(history)

    from core.agent.base_agent import (EFFORT_MAX_TOKENS,
                                       EFFORT_DEFAULT_MAX_TOKENS)
    token_budget = EFFORT_MAX_TOKENS.get(
        str(effort or ""), EFFORT_DEFAULT_MAX_TOKENS)

    from core.agent.turn_engine import run_turn
    try:
        resp, events, turn_out = await run_turn(
            llm_client=llm_client, messages=messages, agent="universe",
            turn_id=turn_id, capability_gate=gate,
            max_tokens=token_budget,
            drain_steer=lambda: [str(m.get("text", ""))
                                 for m in take_steer(turn_id)],
            on_event=on_event)
    except Exception as exc:
        # honest failure path: the transcript records the failure, then the
        # exception propagates to the caller (no silent swallowed turns)
        try:
            store.append(session_id, {
                "kind": "turn_end", "turn_id": turn_id,
                "status": "failed",
                "error": str(exc)[:500],
                "stop_reason": type(exc).__name__})
        except Exception:
            pass
        try:
            timeline_record(turn_id, agent="universe", event="turn_failed",
                            detail=str(exc)[:200])
        except Exception:
            pass
        raise

    reply = str(resp.text or "").strip()
    tools_used, tool_history, tool_failures = _summarize_tool_events(
        events, started_at)

    # ---- append-only: tool trace + final assistant message ------------
    # (a synthetic system trace, NOT dangling role:tool rows — the OpenAI
    # pairing invariant stays valid when the transcript is replayed)
    if tool_history:
        trace_lines = [
            f"[tool {t['tool']} → {'ok' if t['ok'] else 'error'}] "
            f"{t['result_preview'][:300]}" for t in tool_history]
        store.append(session_id, {
            "kind": "tool_trace", "role": "system", "content":
                "[tool activity this turn]\n" + "\n".join(trace_lines),
            "turn_id": turn_id})
    if reply:
        store.append(session_id, {
            "kind": "message", "role": "assistant", "content": reply,
            "turn_id": turn_id, "engine": "session"})
    store.append(session_id, {
        "kind": "turn_end", "turn_id": turn_id, "status": "completed",
        "stop_reason": str(turn_out.stop_reason),
        "steps": turn_out.steps, "tool_calls_total": turn_out.tool_calls_total})

    # ---- the same truth envelope: verified tool results are the record -
    from core.agent.base_agent import HandoffPackage, attach_tool_events
    pkg = attach_tool_events(
        HandoffPackage(what_was_done=reply[:1000], agent="universe"), events)
    pkg.is_knowledge_based = bool(tools_used) or pkg.is_knowledge_based

    completed_at = time.time()
    # measured, not faked: the final completion's own usage counts (m3)
    in_t = int(getattr(resp, "input_tokens", 0) or 0)
    out_t = int(getattr(resp, "output_tokens", 0) or 0)
    try:
        llm_calls = len(llm_client.call_log())
    except Exception:
        llm_calls = 0

    summary: dict[str, Any] = {
        "turn_id": turn_id,
        "session_id": session_id,
        "engine": "session",
        "goal_text": str(text),
        "classification": {"category": "general", "domain": "general",
                           "intent": "agent_turn", "complexity": "adaptive"},
        "success": bool(pkg.task_success) or bool(reply),
        "conversation": True,
        "reply": reply,
        "steps": [{"agent": "universe",
                   "what": "agent turn" + (" with tools" if tools_used else ""),
                   "success": bool(pkg.task_success) or bool(reply),
                   "tools_used": tools_used}],
        "artifacts": list(pkg.artifacts_created),
        "tools_used": tools_used,
        "tools_failed": tool_failures,
        "tool_history": tool_history,
        "todos": [], "todos_idx": None,
        "started_at": started_at,
        "completed_at": completed_at,
        "input_tokens": in_t, "output_tokens": out_t,
        "llm_calls": llm_calls,
        "elapsed_ms": int((completed_at - started_at) * 1000),
        "turn": {"steps": turn_out.steps,
                 "stop_reason": turn_out.stop_reason,
                 "tool_calls_total": turn_out.tool_calls_total},
        "acceptance": {"success": bool(pkg.task_success) or bool(reply),
                       "mode": "session_turn",
                       "rule": "tool results and the agent's own words are the record"},
    }
    timeline_record(turn_id, agent="universe", event="agent_complete",
                    detail=(reply[:220] or "no text"))
    try:
        timeline_record(turn_id, agent="universe", event="turn_complete",
                        detail=json.dumps({"success": summary["success"],
                                           "tools": tools_used,
                                           "steps": turn_out.steps,
                                           "stop_reason": turn_out.stop_reason}))
    except Exception:
        pass
    try:
        from observability.events import emit
        emit("chat_reply", turn_id=turn_id, reply=reply, via="session_turn")
    except Exception:
        pass
    await _maybe_step({"phase": "turn_complete", "reply": reply[:200],
                       "status": "completed"})
    return summary
