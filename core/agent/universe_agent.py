"""core/agent/orchestrator.py — the Orchestrator (the one ZCode-model agent).

The single mind behind the chat. Every message is a TURN of one
professional agent that owns every tool, every skill and the machine's
memory. The turn runs on the ZCode turn engine (core/agent/turn_engine.py —
REA-verified verbatim: unbounded model↔tools loop, turn FSM, dependency-
aware parallel tool scheduler, microcompact + autocompact with the
rapid-refill breaker between steps, steering drained at step boundaries,
tool-driven turn control). The goal-era machinery is DELETED (user order
2026-10-28): no /goal mode, no goal annex, no 400-round budget, no
classification call — a task is a task, and one engine runs it all.

1. run_turn = one turn: durable row insert → session history → live memory
   recall (skills/knowledge/rules) → the turn engine.
2. Loop end: the turn closes in one DB write; tool turns run the full
   memory cycle (gravity, asteroid, promotion, L5) — conversation-only
   turns skip it (memory is for work knowledge, not small talk).

Every tool call from every agent still passes through the Capability Gate —
the gate is enforced structurally because the Orchestrator is the only thing
that hands tools to agents. The Subconscious Loop yields entirely to any
active turn.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from core.agent.base_agent import (
    EFFORT_DEFAULT_MAX_TOKENS,
    EFFORT_MAX_TOKENS,
    UniverseMeta,
    HandoffPackage,
    attach_tool_events,
    new_id,
)
from core.acceptance import evaluate_goal
from core.core_agents import get_agent
from core.agent.turn_engine import LLMOutputLimitExceeded
from core.memory import UniverseMemory, get_memory
from failure.checkpoint import checkpoint, load_checkpoint
from observability.timeline import record as timeline_record
AGENT_TURN_SYSTEM = """You are {name} — a professional agent (the Claude Code / ZCode model):
a single mind that owns every tool, every skill, and the memory of this
machine. You talk to the user directly, in {language}.

1. THIS IS ORDINARY AGENT WORK. Treat the message as a normal professional
   request, exactly like Claude Code treats one: no goal framing, no
   plan announcements for simple work, never call the request "a goal",
   never narrate ceremony — think in facts, act on them.
2. CONVERSATION IS NATURAL. Greetings, questions and comments get plain,
   direct answers. Never announce work, print plans or touch tools when
   nothing needs doing — a "hi" costs one reply and nothing else.
3. ACT WHEN ACTING IS ASKED. Anything that must actually happen — a file,
   a command, research, a page, a message, an install — is done with your
   real tools, one step at a time, reading every tool result before the
   next move. Never describe an action you did not perform, and never
   print tool-call syntax as text: call the tool.
4. MULTI-STEP WORK IS PLANNED LIVE. When the work genuinely has more than
   two steps, write the plan with TodoWrite (the whole list every write)
   and flip each item's status as you go — the Progress panel shows it
   live. Smaller work just gets done.
5. SKILLS ARE DISABLED — proceed without them (no Skill search needed).
6. VERIFY BEFORE FINISHING. A write result, an exit code, test output —
   tool results are the only proof your work exists. If something failed,
   say exactly what failed, then stop; do not keep hammering a failing
   call.
7. Long shell work goes to a background task and you wait for its result.
   A blocked tool (consent/gate) is a decision point: say what was
   blocked, then take the next path. Secrets, destructive and
   irreversible actions go through the consent gate — never route around it.
"""



# Round-7 CC parity (claude binary @206814953 live annex, adapted to the
# gate's reality): plan mode here blocks every write-capable tool, so the
# plan lives in the turn + TodoWrite; the plan FILE is the first post-
# approval action.
PLAN_MODE_ANNEX = """
PLAN MODE — this session is in plan mode: research read-only, design, and
wait for approval. Write-capable tools are blocked by the gate until then.
- Phase 1 (understanding): read-only research only — Read/Grep/Glob/LS, the
  web tools, and read-only specialists. Start broad, then verify specifics
  by reading the actual code/files yourself before designing.
- Phase 2 (design): design the implementation in as much detail as the
  task needs. For trivial changes (typo fixes, one-line changes, simple
  renames) design immediately. For real work, lay out exact steps, the
  files to touch, and how each step will be verified.
- Your turn should only end with either using a research tool OR calling
  ExitPlanMode — do not stop for any other reason.
- AskUserQuestion is ONLY for clarifying requirements or choosing between
  approaches. Approval phrases ("Is this plan okay?", "Should I proceed?",
  "How does this look?") MUST go through ExitPlanMode instead.
- After approval your first actions are: write the plan file under
  {project_root}/.zcode/plans/<task>.md, commit the TodoWrite list for the
  phases, then execute them one by one with verification.
"""

# Round-7 CC sections distilled (care @203291451, tone @203298536,
# act_dont_rederive, context-management @203290000) — Universe keeps its own
# identity/agent-turn block; this carries the CC working-culture additions.
WORKING_AGREEMENTS = """
WORKING AGREEMENTS (how you act):
- Actions with care: gauge reversibility and blast radius before acting;
  run `git status` before any command that could discard uncommitted work;
  irreversible or external-affecting actions go through the consent gate —
  measure twice, cut once.
- Tone: short and concise; reference code as file_path:line_number so it's
  clickable; emojis only if the user explicitly asks.
- When you have enough information to act, act — do not re-derive facts
  already established, re-litigate a decision the user made, or narrate
  options you will not pursue.
- Context management: when the conversation grows long, old turns are
  summarized and work continues — you don't need to wrap up early or hand
  off mid-task; use TodoRead to re-ground after a summary.
- Skill commands: when the user types /<skill-name>, invoke it through the
  Skill tool. Use only skills surfaced to you (see RELEVANT SKILLS); a
  miss returns suggestions — use them instead of guessing.
"""



def record_provider_halt(turn_id: str, reason: str, *,
                         error: str = "",
                         error_kind: str = "",
                         steps: list[dict[str, Any]] | None = None,
                         remaining_plan: list[dict[str, Any]] | None = None,
                         goal_text: str = "",
                         language: str = "en",
                         classification: dict[str, Any] | None = None,
                         handoffs: list[dict[str, Any]] | None = None,
                         planet_id: str = "",
                         owner_session_id: str = "") -> dict[str, Any]:
    """ONE choke point for every provider-halt (was: two inline copies with
    drifting reason formats and emit sequences). Records the timeline, emits
    provider_halt + turn_failed, fails the DB row, writes the resume
    checkpoint, and returns the terminal summary dict. Honesty contract
    (test_provider_routing_e2e): the user-visible error is the RAW provider
    error — never a re-labeled editorial prelude; the failure class rides
    the event kwargs (kind), next to it, not inside the text."""
    timeline_record(turn_id, agent="universe",
                    event="provider_halt", detail=reason)
    try:
        from observability.events import emit
        emit("provider_halt", turn_id=turn_id, reason=reason,
             error=(error or reason)[:300], kind=error_kind)
        emit("turn_failed", turn_id=turn_id, error=reason)
    except Exception:
        pass
    st = get_storage()
    with st.transaction() as conn:
        conn.execute(
            "UPDATE goals SET status='failed', completed_at=?, "
            "final_summary=? WHERE id=?;",
            (time.time(), json.dumps({
                "success": False, "halted": True,
                "error": reason, "steps": steps or [], "artifacts": []}),
             turn_id))
    checkpoint(turn_id, state={"status": "failed", "goal_text": goal_text,
                               "language": language,
                               "owner_session_id": owner_session_id,
                               "classification": classification or {},
                               "planet_id": planet_id,
                               "handoffs": handoffs or [],
                               "remaining_plan": remaining_plan or [],
                               "error": reason})
    return {"turn_id": turn_id, "goal_text": goal_text,
            "classification": classification or {}, "success": False,
            "halted": True, "error": reason,
            "steps": steps or [], "artifacts": [], "elapsed_ms": 0,
            "acceptance": {"success": False, "failure": reason}}
from providers.client import LLMClient, LLMError, get_llm_client
from providers.manager import get_provider_manager
from security.capability import get_gate
from storage.local import get_storage

# NOTE: Classification/chat routing removed — ZCode happy path: every message is a turn.
# The turn engine itself returns no_tool_calls for pure conversation; no pre-routing needed.
# Deleted: CLASSIFY_SYSTEM, _CHAT_PATTERNS, _WORK_MARKERS, _WORK_IDIOM_RE, _contains_work_intent,
# _FAKE_TOOL_PATTERNS, _looks_like_chat etc. (see git history before v26).

class UniverseAgent:
    """The single mind. One instance per process."""

    def __init__(self, *, memory: UniverseMemory | None = None,
                 llm_client: LLMClient | None = None) -> None:
        self.memory = memory or get_memory()
        self.llm = llm_client or get_llm_client()
        self._pm = get_provider_manager()
        self._st = get_storage()
        self._active_turns: set[str] = set()
        self._active_turn_ids = self._active_turns  # alias
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._turn_locks = self._turn_locks  # alias
        self._turn_lock_refs: dict[str, int] = {}
        self._goal_lock_refs = self._turn_lock_refs  # alias

    # ---- the main goal lifecycle -----------------------------------------
    # Progress callbacks share ONE contract: a sync-or-async callable
    # taking a single JSON-able dict payload. on_step receives
    # {"phase": <name>, ...}; on_handoff receives a HandoffPackage dump.
    # (The capability-gate side was already typed; the orchestrator side
    # was bare Any — same shape, now stated in one place.)
    StepHandler = Any  # Callable[[dict[str, Any]], Any] — sync or async
    HandoffHandler = Any  # Callable[[dict[str, Any]], Any] — sync or async

    # ---- the session engine (default) --------------------------------------
    async def _run_session_turn(self, *, text: str, language: str,
                                turn_id: str, on_step, capability_gate,
                                owner_session_id: str, project_id,
                                project_root, session_id,
                                effort) -> dict[str, Any]:
        """The session-engine turn (the Claude Code / Codex contract).

        The session transcript is the ONLY durable state: no goals rows,
        no planets, no gravity — the turn is appended to the transcript,
        replayed as context, and closed with a turn_end event. The tool
        loop still runs on the same ZCode turn engine and the Capability
        Gate still guards every call. GAX_ENGINE=goal restores the legacy
        goal-state path until it is deleted after the burn-in period."""
        import os as _os
        from core.session.store import get_session_store
        store = get_session_store()
        sid = str(session_id or "").strip()
        if sid and store.get(sid) is None:
            try:
                store.create(id=sid, title=str(text)[:120])
            except Exception:
                pass
        if not sid or store.get(sid) is None:
            sid = store.create(title=str(text)[:120])["id"]
        store.append(sid, {"kind": "turn_start", "turn_id": turn_id,
                           "status": "executing"})
        store.append(sid, {"kind": "message", "role": "user",
                           "content": str(text), "turn_id": turn_id})
        if on_step:
            await self._maybe_call(on_step, {"phase": "agent_turn",
                                             "agent": "universe", "step": 0,
                                             "instruction": str(text)[:200]})
        history = store.history_messages(sid)
        effective_root = str(project_root or "")
        if not effective_root and project_id:
            try:
                from storage.local import get_storage as _gs
                _pr = _gs().query_one("SELECT root FROM projects WHERE id=?",
                                      (project_id,))
                if _pr and _pr.get("root"):
                    effective_root = str(_pr["root"])
            except Exception:
                pass
        if not effective_root:
            try:
                from config import get_config
                effective_root = str(get_config().get("project_root", "") or "")
            except Exception:
                effective_root = ""
        messages = self._build_turn_messages(
            goal_text=str(text), language=str(language or "en"),
            history=history[:-1], l4_skills=[], l3_stars=[],
            rules=self.memory.active_rules(), project_root=effective_root)
        from core.agent.steer import take_steer as _take_steer
        token_budget = EFFORT_MAX_TOKENS.get(
            str(effort or ""), EFFORT_DEFAULT_MAX_TOKENS)
        gate = capability_gate or get_gate()
        from core.agent.turn_engine import run_turn as _engine_turn
        try:
            resp, events, turn_out = await _engine_turn(
                llm_client=self.llm, messages=messages, agent="universe",
                turn_id=turn_id, capability_gate=gate,
                max_tokens=token_budget,
                drain_steer=lambda: [str(m.get("text", ""))
                                     for m in _take_steer(turn_id)])
        except Exception as e:                                   # honest failure
            store.append(sid, {"kind": "turn_end", "turn_id": turn_id,
                               "status": "failed", "stop_reason": "error",
                               "error": str(e)[:500]})
            return {"engine": "session", "session_id": sid,
                    "turn_id": turn_id, "success": False,
                    "reply": f"Turn failed: {e}", "steps": [],
                    "tools_used": []}
        reply = str(resp.text or "").strip()
        for _ev in events:          # tool protocol atoms ride in the transcript
            if _ev.get("tool"):
                store.append(sid, {"kind": "tool", "role": "tool",
                                   "tool_call_id": f"{_ev.get('tool')}:{_ev.get('seq', '')}",
                                   "content": json.dumps(_ev.get("args") or {},
                                                         ensure_ascii=False)[:4000]})
        store.append(sid, {"kind": "message", "role": "assistant",
                           "content": reply, "turn_id": turn_id})
        store.append(sid, {"kind": "turn_end", "turn_id": turn_id,
                           "status": "completed"})
        return {"engine": "session", "session_id": sid, "turn_id": turn_id,
                "success": True, "reply": reply, "goal_text": str(text),
                "tools_used": sorted({str(e.get("tool", "")) for e in events
                                      if e.get("tool")}),
                "steps": [], "tool_failures": [],
                "input_tokens": int(getattr(resp, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(resp, "output_tokens", 0) or 0),
                "llm_calls": 1}

    async def run_turn(self, text: str, *, language: str = "en",
                       turn_id: str | None = None,
                       on_step: StepHandler = None,
                       on_handoff: HandoffHandler = None,
                       capability_gate=None,
                       owner_session_id: str = "",
                       project_id: str | None = None,
                       project_root: str | None = None,
                       title: str | None = None,
                       session_id: str | None = None,
                       parent_goal_id: str | None = None,
                       goal_id: str | None = None,
                       effort: str | None = None) -> dict[str, Any]:
        """Execute one turn with durable state transitions and the ZCode
        turn engine (unbounded; the operator cap GAX_TOOL_MAX_ROUNDS still
        applies when set). An explicit turn_id lets frontends (the desktop
        bridge) correlate the turn with their own request before the first
        await yields. project_id/title ride along as metadata (sidebar
        grouping) so callers stop issuing a side-UPDATE after the fact.
        session_id: set when this turn is a MESSAGE inside a task
        container (kind='message') — the desktop threading model. The
        title fallback is deliberately NOT the message text: tasks are
        named by the user (rename) or stay "New task", never by content.
        The old goal_mode long-horizon variant is DELETED — one engine
        runs every turn."""
        goal_text = text  # legacy alias support
        if goal_id and not turn_id:
            turn_id = goal_id
        if parent_goal_id and not session_id:
            session_id = parent_goal_id
        turn_id = turn_id or new_id("turn-")
        # Engine selection: the session engine is the DEFAULT (the goal
        # era is over); GAX_ENGINE=goal restores the legacy goal-state
        # path until it is deleted after the burn-in period.
        import os as _os_engine
        engine_mode = (_os_engine.environ.get("GAX_ENGINE", "")
                       or "session").strip().lower()
        if engine_mode != "goal":
            return await self._run_session_turn(
                text=text, language=language, turn_id=turn_id,
                on_step=on_step, capability_gate=capability_gate,
                owner_session_id=owner_session_id, project_id=project_id,
                project_root=project_root, session_id=session_id,
                effort=effort)
        now = time.time()
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT INTO goals(id,text,status,created_at,owner_session_id,"
                "kind,parent_goal_id) VALUES(?,?,?,?,?,?,?);",
                (turn_id, goal_text, "running", now, owner_session_id,
                 "message" if session_id else "task", session_id),
            )
            # project/title are best-effort metadata: older schemas may
            # lack the columns (migration v14 added them) — never fail
            # the goal for a grouping nicety. Title NEVER falls back to
            # the message text (v17: sidebar rows are named by users).
            try:
                conn.execute(
                    "UPDATE goals SET project_id=?, title=? WHERE id=?;",
                    (project_id or "",
                     (title or "New task")[:200], turn_id),
                )
            except Exception:
                pass
        self._active_turn_ids.add(turn_id)
        # setdefault, never overwrite: two run_goal/resume_turn calls with
        # the same turn_id must serialize on ONE lock, not each mint their
        # own (the old plain assignment let concurrent runs proceed in
        # parallel on the same goal — m3 _turn_locks fix)
        lock = self._turn_locks.setdefault(turn_id, asyncio.Lock())
        # refcount (audit 2026-09): popping the lock entry while a second
        # caller still waits on it let a THIRD caller mint a fresh lock and
        # run concurrently — breaking the very serialization the lock exists
        # for. The entry is removed only when the last contender exits.
        self._goal_lock_refs[turn_id] = self._goal_lock_refs.get(turn_id, 0) + 1
        gate = capability_gate or get_gate()
        handoffs: list[dict[str, Any]] = []
        planet = None
        classification: dict[str, str] = {}
        # initialized HERE, before the acquire: the except/cancel handlers
        # below reference them, and a cancellation while WAITING for the
        # goal lock (or any failure before the plan loop) would otherwise
        # crash the handler itself with UnboundLocalError, masking the real
        # outcome
        remaining_plan_now: list[dict[str, Any]] = []
        held = False
        try:
            await lock.acquire()
            held = True
            # ---- THE AGENT TURN (the Claude Code / ZCode model) ------
            # The message IS the input to one professional turn: no
            # classify call, no keyword router, no media/acceptance
            # gates, no goal mode. One agent with the full toolset
            # decides what a turn needs — answering directly for
            # conversation, acting with tools for real work — inside the
            # ZCode turn engine.
            classification = {"category": "general", "domain": "general",
                              "intent": "agent_turn", "complexity": "adaptive"}
            language = str(language or "en")
            # ZCode happy path: planet (L1) is per TASK, not per turn.
            task_key = session_id or turn_id
            existing = self.memory.l1.get_planet_by_goal(task_key)
            if existing is not None:
                planet = existing
            else:
                planet = self.memory.l1.create_planet(
                    turn_id=task_key, goal_text=goal_text,
                    classification={**classification, "mode": "agent_turn"},
                    language=language, owner_session_id=owner_session_id)
            turn_started = time.time()
            timeline_record(turn_id, agent="universe", event="turn_start",
                            detail=goal_text[:200])
            checkpoint(turn_id, state={
                "status": "executing", "goal_text": goal_text,
                "language": language, "owner_session_id": owner_session_id,
                "classification": classification, "planet_id": planet.id,
                "handoffs": [], "remaining_plan": []})
            if on_step:
                await self._maybe_call(on_step, {"phase": "agent_turn",
                                                 "agent": "universe", "step": 0,
                                                 "instruction": goal_text[:200]})
            # this task's earlier turns ride along as the session history
            history = await asyncio.to_thread(
                self._load_turn_history, session_id, turn_id)
            # Persist current user turn to session transcript (single authority) - after history load to avoid duplication
            try:
                from core.session.store import get_session_store
                _ss2 = get_session_store()
                _skey2 = session_id or task_key
                if _skey2:
                    if not _ss2.get(_skey2):
                        try:
                            _ss2.create(id=_skey2, title=goal_text[:120], workspace_root=effective_root if 'effective_root' in locals() else "")
                        except Exception:
                            pass
                    _ss2.append(_skey2, {"role": "user", "content": goal_text, "kind": "message", "turn_id": turn_id})
            except Exception:
                pass
            # memory recall: the skills index + remembered knowledge join
            # the system prompt every turn — synchronous SQLite/BM25 work
            # stays off the event loop
            l4_hits, l3_hits = await asyncio.to_thread(
                lambda: (self.memory.search_l4(goal_text, top_k=6),
                         self.memory.search_l3(goal_text, top_k=5)))
            if l3_hits:
                await asyncio.to_thread(self.memory.l3.touch, l3_hits[0].id)
            # Project root resolution: explicit project_root > lookup via project_id > global config
            effective_root = ""
            if project_root:
                effective_root = str(project_root)
            elif project_id:
                try:
                    from storage.local import get_storage as _gs
                    _pr = _gs().query_one("SELECT root FROM projects WHERE id=?", (project_id,))
                    if _pr and _pr.get("root"):
                        effective_root = str(_pr["root"])
                except Exception:
                    pass
            if not effective_root:
                try:
                    from config import get_config
                    effective_root = str(get_config().get("project_root", "") or "")
                except Exception:
                    effective_root = ""
            # Set ContextVar so all file/shell/capability checks use project root for this turn
            _ctx_token = None
            try:
                from core.agent.project_context import project_root_ctx
                _ctx_token = project_root_ctx.set(effective_root or None)
            except Exception:
                pass
            cfg_root = effective_root
            messages = self._build_turn_messages(
                goal_text=goal_text, language=language, history=history,
                l4_skills=[s.to_dict() for s in l4_hits],
                l3_stars=[s.to_dict() for s in l3_hits],
                rules=self.memory.active_rules(), project_root=cfg_root)
            from core.agent.steer import take_steer as _take_steer
            token_budget = EFFORT_MAX_TOKENS.get(
                str(effort or ""), EFFORT_DEFAULT_MAX_TOKENS)
            timeline_record(turn_id, agent="universe", event="agent_start",
                            detail=goal_text[:200])
            from core.agent.turn_engine import run_turn
            resp, events, turn_out = await run_turn(
                llm_client=self.llm, messages=messages, agent="universe",
                turn_id=turn_id, capability_gate=gate,
                max_tokens=token_budget,
                drain_steer=lambda: [str(m.get("text", ""))
                                     for m in _take_steer(turn_id)])
            reply = str(resp.text or "").strip()
            tools_used = sorted({str(e.get("tool", "")) for e in events
                                 if e.get("tool")})
            tool_failures: list[dict[str, str]] = []
            tool_history: list[dict[str, Any]] = []
            for _ev in events:
                _tool = str(_ev.get("tool", ""))
                try:
                    _args = _ev.get("args") or {}
                    _res = _ev.get("result")
                    if not isinstance(_res, dict):
                        _res = {}
                    _ok = bool(_res.get("ok"))
                    _inner = _res.get("result")
                    if isinstance(_inner, dict) and "ok" in _inner:
                        _ok = bool(_inner.get("ok"))
                    _preview = ""
                    if isinstance(_res.get("result"), str):
                        _preview = str(_res["result"])[:500]
                    elif isinstance(_res.get("error"), str):
                        _preview = str(_res["error"])[:300]
                    elif isinstance(_res, dict):
                        _preview = json.dumps(_res, ensure_ascii=False)[:500]
                    _ap = {k: (str(v)[:200] if isinstance(v, str) else v)
                           for k, v in (dict(_args) if isinstance(_args, dict) else {}).items()}
                    _agent = str(_ev.get("agent") or _ev.get("owner_agent") or "universe")
                    _dur = _ev.get("duration_ms", _ev.get("elapsed_ms", _ev.get("duration", 0)))
                    try:
                        _dur = int(_dur or 0)
                    except Exception:
                        _dur = 0
                    tool_history.append({"tool": _tool, "ok": _ok, "args": _ap, "result_preview": _preview, "seq": _ev.get("seq", 0), "agent": _agent, "duration_ms": _dur, "ts": _ev.get("ts", _ev.get("started_at", turn_started))})
                except Exception:
                    pass
                _res2 = _ev.get("result")
                if not isinstance(_res2, dict):
                    _res2 = {}
                _ok2 = bool(_res2.get("ok"))
                _inner2 = _res2.get("result")
                if isinstance(_inner2, dict) and "ok" in _inner2:
                    _ok2 = bool(_inner2.get("ok"))
                if not _ok2:
                    tool_failures.append({"tool": _tool, "error": str(_res2.get("error", ""))[:200]})
            # the same truth envelope the goal engine always used: artifacts
            # come from VERIFIED tool results, task_success flips on
            # unrecovered failures — prose never becomes proof
            pkg = attach_tool_events(
                HandoffPackage(what_was_done=reply[:1000], agent="universe"),
                events)
            # L3 promotion honesty (engine-fix round): the package built
            # here kept the is_knowledge_based=False default, so gravity's
            # Confidence Accumulation (avg_conf × kb_ratio) was 0 and every
            # primary-path turn scored ≤ 0.40 — below both promotion
            # thresholds (0.45 idle, 0.85 real-time) — while the resume
            # path (_parse_handoff defaults True) graded the same verified
            # work ~0.79. Tool-verified output IS knowledge-based: the
            # events above carry the real evidence.
            pkg.is_knowledge_based = bool(tools_used) or pkg.is_knowledge_based
            completed_at = time.time()
            elapsed_ms = int((completed_at - turn_started) * 1000)
            # Capture current todos for persistence (Bug 4 replay)
            _persist_todos = []
            _persist_todos_idx = None
            try:
                from connectors.builtin.todos import _todos as _todo_ledger
                _persist_todos = list(_todo_ledger.get(turn_id, []))
                # find in_progress idx
                for _ti, _t in enumerate(_persist_todos):
                    if _t.get("status") == "in_progress":
                        _persist_todos_idx = _ti
                        break
            except Exception:
                pass
            summary: dict[str, Any] = {
                "turn_id": turn_id, "goal_text": goal_text,
                "classification": classification,
                "success": bool(pkg.task_success),
                "conversation": True, "reply": reply,
                "steps": [{"agent": "universe",
                           "what": "agent turn" + (" with tools" if tools_used else ""),
                           "success": bool(pkg.task_success),
                           "tools_used": tools_used}],
                "artifacts": list(pkg.artifacts_created),
                "tools_used": tools_used, "tools_failed": tool_failures,
                "tool_history": tool_history,
                "todos": _persist_todos,
                "todos_idx": _persist_todos_idx,
                "started_at": turn_started,
                "completed_at": completed_at,
                "llm_calls": len(self.llm.call_log()),
                "elapsed_ms": elapsed_ms,
                "turn": {"steps": turn_out.steps,
                         "stop_reason": turn_out.stop_reason,
                         "tool_calls_total": turn_out.tool_calls_total},
                "acceptance": {"success": bool(pkg.task_success),
                               "mode": "agent_turn",
                               "rule": "tool results and the agent's own words are the record"},
            }
            if tools_used:
                # a turn that touched real tools DID work: run the full
                # memory cycle (gravity, asteroid, promotion, L5) exactly
                # as work goals always did
                asteroid = await asyncio.to_thread(
                    self.memory.complete_task, planet=planet,
                    handoffs=[pkg.model_dump()],
                    classification=classification, language=language)
                summary["gravity_score"] = round(asteroid.gravity_score, 4)
                summary["gravity_bucket"] = _bucket(asteroid.gravity_score)
                summary["asteroid_id"] = asteroid.id
                summary["promoted_to_l3"] = bool(asteroid.promoted_to)
            else:
                summary["gravity_score"] = 0.1
            timeline_record(turn_id, agent="universe", event="agent_complete",
                            detail=(reply[:220] or "no text"))
            try:
                from observability.events import emit
                emit("chat_reply", turn_id=turn_id, reply=reply,
                     via="agent_turn")
            except Exception:
                pass
            # the terminal event rides the timeline mirror exactly as work
            # turns always did (turn_complete -> turn_completed)
            timeline_record(turn_id, agent="universe", event="turn_complete",
                            detail=json.dumps({"success": summary["success"],
                                               "tools": tools_used,
                                               "steps": turn_out.steps,
                                               "stop_reason": turn_out.stop_reason}))
            goal_status = ("completed" if (reply or not tool_failures)
                           else "failed")
            with self._st.transaction() as conn:
                conn.execute(
                    "UPDATE goals SET status=?, completed_at=?, final_summary=? "
                    "WHERE id=?;",
                    (goal_status, time.time(), json.dumps(summary), turn_id))
                conn.execute(
                    "UPDATE planets SET status='completed', updated_at=? "
                    "WHERE turn_id=?;", (time.time(), task_key))
                checkpoint(turn_id, state={
                    "status": goal_status, "goal_text": goal_text,
                    "language": language, "owner_session_id": owner_session_id,
                    "classification": classification, "planet_id": planet.id,
                    "handoffs": [], "remaining_plan": [], "summary": summary})
                # Append assistant turn + tool history to session transcript (single authority)
                try:
                    from core.session.store import get_session_store as _gss2
                    _ss2 = _gss2()
                    _sk = session_id or session_id or task_key
                    if _sk and reply:
                        _ss2.append(_sk, {"role": "assistant", "content": reply, "kind": "message", "turn_id": turn_id})
                    # naive tool_calls preservation: each tool event as tool result entry
                    for _ev in events:
                        if _ev.get("tool"):
                            _ss2.append(_sk, {"role": "tool", "tool_call_id": str(_ev.get("seq", "")), "content": str((_ev.get("result") or {}).get("result") or (_ev.get("result") or {}).get("error") or "")[:2000], "kind": "tool", "turn_id": turn_id, "tool": _ev.get("tool")})
                except Exception:
                    pass
                if on_step:
                    await self._maybe_call(on_step, {"phase": "turn_complete",
                                                     "reply": reply[:200],
                                                     "status": goal_status})
                if _ctx_token is not None:
                    try:
                        from core.agent.project_context import project_root_ctx as _pc2
                        _pc2.reset(_ctx_token)
                    except Exception:
                        pass
                return summary
        except asyncio.CancelledError:
            try:
                from observability.events import emit
                emit("turn_cancelled", turn_id=turn_id, reason="user")
            except Exception:
                pass
            self._mark_goal_cancelled(turn_id)
            # planet is None when the cancel landed before planet creation
            # (e.g. during classify) — the old handler dereferenced
            # planet.id unconditionally, raised AttributeError FROM the
            # except block, and replaced the CancelledError so the goal
            # read "failed" instead of "cancelled" (audit 2026-09)
            checkpoint(turn_id, state={"status": "cancelled", "goal_text": goal_text,
                                       "classification": classification, "handoffs": handoffs,
                                       "remaining_plan": remaining_plan_now, "language": language,
                                       "owner_session_id": owner_session_id,
                                       "planet_id": planet.id if planet is not None else ""})
            raise
        except LLMError as exc:
            # S1d: a terminal provider failure halts the turn honestly — the
            # old in-loop sanity guard lived inside the deleted ladder loop.
            # The RAW provider error is the reason (never re-labeled); the
            # classified kind rides the provider_halt event kwargs.
            reason = str(exc)[:500]
            return record_provider_halt(
                turn_id, reason, error=str(exc)[:500],
                error_kind=getattr(exc, "kind", "unknown") or "unknown",
                goal_text=goal_text, language=language,
                owner_session_id=owner_session_id)
        except LLMOutputLimitExceeded as exc:
            # ZCode's model_output_limit_exceeded: the turn is recoverable
            # provider-side but cannot continue here — surface it as the
            # provider halt it is.
            reason = str(exc)[:500]
            return record_provider_halt(
                turn_id, reason, error=reason,
                error_kind="model_output_limit_exceeded",
                goal_text=goal_text, language=language,
                owner_session_id=owner_session_id)
        except Exception as exc:
            self._mark_goal_failed(turn_id, str(exc))
            # structured provider-failure signal (m3-4): LLMError.kind carries
            # no_provider/rate_limit/timeout/provider_down/auth… up from
            # providers/client.py so summaries can classify the failure
            # instead of string-matching prose
            error_kind = getattr(exc, "kind", "")
            checkpoint(turn_id, state={"status": "failed", "goal_text": goal_text,
                                       "classification": classification, "handoffs": handoffs,
                                       "remaining_plan": remaining_plan_now, "error": str(exc),
                                       "error_kind": error_kind})
            timeline_record(turn_id, agent="universe", event="turn_failed",
                            detail=json.dumps({"error": str(exc), "error_kind": error_kind}))
            raise
        finally:
            # Reset project context for next turn
            try:
                if _ctx_token is not None:
                    from core.agent.project_context import project_root_ctx as _pc3
                    _pc3.reset(_ctx_token)
            except Exception:
                pass
            # the goal is over: any steer still queued can never be applied —
            # drop it so nothing leaks into a future run of the same id
            try:
                from core.agent.steer import clear as _clear_steer
                _clear_steer(turn_id)
                from core.agent.swarm import clear as _clear_swarm
                _clear_swarm(turn_id)
                # hooks one-shot state (require_facts) is keyed per goal —
                # clear it here too so the manager never accumulates rows
                # for ended goals (engine-fix round)
                from hooks import get_hook_manager
                get_hook_manager().clear_goal_state(turn_id)
            except Exception:
                pass
            # held-guarded release: `if lock.locked(): release()` released
            # OTHER holders' locks when this coroutine was cancelled while
            # waiting (asyncio locks have no owner check)
            if held:
                try:
                    lock.release()
                except RuntimeError:
                    pass
            self._active_turn_ids.discard(turn_id)
            refs = self._goal_lock_refs.get(turn_id, 1) - 1
            if refs <= 0:
                self._goal_lock_refs.pop(turn_id, None)
                self._turn_locks.pop(turn_id, None)
            else:
                self._goal_lock_refs[turn_id] = refs

    def _load_turn_history(self, parent_task_id: str | None,
                           exclude_goal_id: str) -> list[dict[str, str]]:
        """Session authority: primary is transcript.jsonl, fallback to goals for legacy."""
        if not parent_task_id:
            return []
        # Try session store first (full authority)
        try:
            from core.session.store import get_session_store
            sess_hist = get_session_store().history_messages(parent_task_id)
            if sess_hist:
                # history_messages already returns role/content with tool_calls, bounded
                out_sess: list[dict[str, str]] = []
                for m in sess_hist[-24:]:
                    c = str(m.get("content") or "").strip()
                    if c:
                        out_sess.append({"role": str(m.get("role") or "user"), "content": c[:4000]})
                if out_sess:
                    return out_sess
        except Exception:
            pass
        try:
            rows = self._st.query_all(
                "SELECT text, status, final_summary FROM goals WHERE "
                "session_id=? AND kind='message' AND id<>? "
                "ORDER BY created_at ASC;",
                (parent_task_id, exclude_goal_id))
        except Exception:
            return []
        out: list[dict[str, str]] = []
        for r in (rows[-24:] if isinstance(rows, list) else rows):
            text = str(r.get("text") or "").strip()
            if text:
                out.append({"role": "user", "content": text[:4000]})
            reply = ""
            if r.get("final_summary"):
                try:
                    fs = json.loads(r["final_summary"])
                except Exception:
                    fs = {}
                if isinstance(fs, dict):
                    reply = str(fs.get("reply") or "")
                    if not reply:
                        steps = fs.get("steps") or []
                        if isinstance(steps, list):
                            reply = "; ".join(
                                str(st.get("what", "")) for st in steps
                                if isinstance(st, dict)
                                and str(st.get("what", "")).strip())
            if reply.strip():
                out.append({"role": "assistant", "content": reply[:4000]})
        return out

    @staticmethod
    def _build_turn_messages(*, goal_text: str, language: str,
                             history: list[dict[str, str]],
                             l4_skills: list[dict[str, Any]],
                             l3_stars: list[dict[str, Any]],
                             rules: list[dict[str, Any]],
                             project_root: str) -> list[dict[str, Any]]:
        """The ZCode-style turn prompt: identity + live memory
        (relevant skills, remembered knowledge, standing rules) + the
        session history + this message. Collected memory is trusted; the
        user text and history are untrusted data, never instructions."""
        try:
            from config import get_config
            agent_name = str(get_config().get("desktop", {}).get(
                "assistant_name", "") or "Universe")
        except Exception:
            agent_name = "Universe"
        parts: list[str] = [
            AGENT_TURN_SYSTEM.format(name=agent_name,
                                     language=language or "en")]
        try:
            from security.capability import get_gate
            if get_gate().get_permission_mode() == "plan":
                parts.append(PLAN_MODE_ANNEX.format(
                    project_root=project_root or "."))
        except Exception:
            pass
        parts.append(WORKING_AGREEMENTS)
        if project_root:
            parts.append(
                "PROJECT ROOT: " + project_root + "\n"
                "shell.exec uses this directory as its default cwd; prefer "
                "relative paths under it for every file/command tool.")
        if l4_skills:
            skill_lines = []
            for s in l4_skills:
                body = str(s.get("body", "")).strip()
                if len(body) > 2400:
                    body = body[:2400] + " …"
                skill_lines.append(
                    "- " + str(s.get("name", "")) + " | "
                    + str(s.get("description", "")) + "\n  procedure="
                    + body)
            parts.append("RELEVANT SKILLS (trusted data):\n"
                         + "\n".join(skill_lines))
        if l3_stars:
            parts.append(
                "REMEMBERED KNOWLEDGE (trusted data):\n" + "\n".join(
                    "- " + str(s.get("topic", "")) + ": "
                    + str(s.get("summary", ""))[:160] for s in l3_stars))
        if rules:
            parts.append(
                "Standing rules:\n" + "\n".join(
                    "- [" + str(r.get("kind", "")) + "] "
                    + str(r.get("rule", "")) for r in rules[:8]))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "\n\n".join(p for p in parts if p)}]
        messages.extend(history)
        messages.append({"role": "user", "content": goal_text})
        return messages


    def _mark_goal_failed(self, turn_id: str, error: str) -> None:
        with self._st.transaction() as conn:
            conn.execute("UPDATE goals SET status=?, completed_at=?, final_summary=? WHERE id=?;",
                         ("failed", time.time(), json.dumps({"error": error}), turn_id))

    def _mark_goal_cancelled(self, turn_id: str) -> None:
        # a cancelled goal must read 'cancelled' in the durable row too:
        # writing 'failed' here fought the bridge's live 'cancelled' status
        # and the row flipped once the bridge TTL-evicted the live entry
        with self._st.transaction() as conn:
            conn.execute("UPDATE goals SET status=?, completed_at=?, final_summary=? WHERE id=?;",
                         ("cancelled", time.time(),
                          json.dumps({"error": "cancelled by user",
                                      "cancelled": True}), turn_id))

    async def resume_turn(self, turn_id: str, *, on_step: Any = None,
                          capability_gate=None,
                          effort: str | None = None) -> dict[str, Any]:
        """Resume a cancelled/failed goal from its checkpoint.

        The retired multi-step ladder stored `remaining_plan` steps to
        replay; in the single-agent architecture nothing populates it, so
        resume now re-runs the goal as ONE fresh universe-agent run with the
        prior attempt's real evidence (artifacts, decisions, failures)
        injected as the starting handoff. The goal moves again instead of
        being refused by an empty-plan guard that could never pass
        (audit 2026-09: resume was doubly dead — a nonexistent
        _record_partial_handoff call crashed it, and every checkpoint the
        current code writes carries remaining_plan: [])."""
        cp = load_checkpoint(turn_id)
        if not cp:
            raise ValueError(f"no checkpoint for {turn_id}")
        goal_text = str(cp.get("goal_text") or "")
        if not goal_text:
            raise ValueError(f"checkpoint for {turn_id} has no goal text")
        if str(cp.get("status") or "") == "completed":
            # resume_from (failure/checkpoint.py) repeats this exact refusal
            # for the CLI path — a deliberate double-check on the shared
            # entry point (engine-fix round: one truth lives in each
            # caller; keep the two in sync if the policy ever changes)
            raise ValueError(f"goal {turn_id} already completed")
        planet = self.memory.l1.get_planet_by_goal(turn_id)
        if planet is None:
            raise ValueError(f"planet for {turn_id} not found")
        classification = dict(cp.get("classification")
                              or planet.session_context.get("classification", {}))
        language = str(cp.get("language")
                       or planet.session_context.get("language", "en"))
        prior_handoffs = list(cp.get("handoffs") or [])
        prior_summary = dict(cp.get("summary") or {})
        gate = capability_gate or get_gate()

        # continuation handoff: the prior attempt's durable evidence
        prior_failures = [str(h.get("what_was_done", ""))[:200]
                          for h in prior_handoffs
                          if not h.get("task_success", True)]
        handoff = HandoffPackage(
            what_was_done=str(prior_summary.get("reply")
                              or prior_summary.get("what_was_done") or
                              ("prior attempt stopped (" +
                               str(cp.get("status") or "unknown") + ")")),
            key_decisions=[f"resuming goal previously {cp.get('status', 'stopped')}"],
            artifacts_created=[a for h in prior_handoffs
                               for a in (h.get("artifacts_created") or [])][:20],
            avoid_these=prior_failures[:5],
            context_for_memory={"resumed_from": turn_id,
                                "prior_error": str(cp.get("error") or "")[:500]},
            next_agent_focus="Continue and finish the goal",
            agent="universe",
        )
        handoffs = list(prior_handoffs)
        self._active_turn_ids.add(turn_id)
        lock = self._turn_locks.setdefault(turn_id, asyncio.Lock())
        self._goal_lock_refs[turn_id] = self._goal_lock_refs.get(turn_id, 0) + 1
        held = False
        try:
            await lock.acquire()
            held = True
            checkpoint(turn_id, state={"status": "resuming", "goal_text": goal_text,
                                       "language": language, "classification": classification,
                                       "planet_id": planet.id, "handoffs": handoffs,
                                       "remaining_plan": []})
            if on_step:
                await self._maybe_call(on_step, {"phase": "resume_agent_start",
                                                 "agent": "universe"})
            # memory recall joins the context off the event loop — same
            # to_thread discipline run_goal uses (engine-fix round)
            def _recall() -> dict[str, Any]:
                return {
                    "l4_skills": [s.to_dict() for s in self.memory.search_l4(
                        goal_text, category=classification.get("category", ""),
                        domain=classification.get("domain", ""), top_k=5)],
                    "l3_stars": [s.to_dict() for s in self.memory.search_l3(
                        goal_text, top_k=3)],
                    "active_rules": self.memory.active_rules(),
                }
            recalled = await asyncio.to_thread(_recall)
            context = {**recalled,
                       "handoff": handoff.model_dump(),
                       "classification": classification, "untrusted": [],
                       "effort": effort}
            pkg = await get_agent("universe").run(
                memory=self.memory, llm_client=self.llm, goal_text=goal_text,
                classification=classification, handoff=handoff, context=context,
                capability_gate=gate, turn_id=turn_id)
            handoffs.append(pkg.model_dump())
            acceptance = await asyncio.to_thread(
                evaluate_goal, goal_text=goal_text, classification=classification,
                handoffs=handoffs)
            self._apply_acceptance(handoffs, acceptance, turn_id=turn_id)
            meta = UniverseMeta(category=classification.get("category", "general"),
                              domain=classification.get("domain", "general"),
                              intent=classification.get("intent", "write"),
                              complexity=classification.get("complexity", "medium"),
                              classification=classification)
            asteroid = await asyncio.to_thread(
                self.memory.complete_task, planet=planet, handoffs=handoffs,
                classification=classification, language=language)
            summary = self._build_summary(turn_id, goal_text, asteroid, handoffs, meta, acceptance)
            with self._st.transaction() as conn:
                conn.execute("UPDATE goals SET status=?, completed_at=?, final_summary=? WHERE id=?;",
                             ("completed" if asteroid.task_success else "failed", time.time(),
                              json.dumps(summary), turn_id))
            checkpoint(turn_id, state={"status": "completed" if summary["success"] else "failed",
                                       "goal_text": goal_text, "language": language,
                                       "classification": classification, "planet_id": planet.id,
                                       "handoffs": handoffs, "remaining_plan": [], "summary": summary})
            return summary
        finally:
            # goal over: drop the per-goal in-memory state exactly as
            # run_goal does (steer queue, swarm bookkeeping + mailboxes,
            # hook one-shots) — resume previously leaked all three
            try:
                from core.agent.steer import clear as _clear_steer
                _clear_steer(turn_id)
                from core.agent.swarm import clear as _clear_swarm
                _clear_swarm(turn_id)
                from hooks import get_hook_manager
                get_hook_manager().clear_goal_state(turn_id)
            except Exception:
                pass
            if held:
                try:
                    lock.release()
                except RuntimeError:
                    pass
            self._active_turn_ids.discard(turn_id)
            refs = self._goal_lock_refs.get(turn_id, 1) - 1
            if refs <= 0:
                self._goal_lock_refs.pop(turn_id, None)
                self._turn_locks.pop(turn_id, None)
            else:
                self._goal_lock_refs[turn_id] = refs

    # ---- default plan -----------------------------------------------------
    @staticmethod
    def _apply_acceptance(handoffs: list[dict[str, Any]], acceptance: dict[str, Any], *, turn_id: str = "") -> None:
        """Make acceptance and lifecycle evidence authoritative over model prose.

        Honesty rule: a passing acceptance may NEVER rewrite a failed
        handoff into a success. Failed steps stay failed in the record —
        the goal outcome itself is acceptance's call, but the per-step
        history must remain truthful for the user to audit."""
        if not handoffs:
            return
        final = handoffs[-1]
        ctx = final.setdefault("context_for_memory", {})
        ctx["acceptance_evidence"] = acceptance
        try:
            from observability.tool_lifecycle import lifecycle_snapshot
            ctx["tool_lifecycle_evidence"] = lifecycle_snapshot(turn_id)
        except Exception:
            ctx["tool_lifecycle_evidence"] = []
        if not acceptance.get("success", False):
            final["task_success"] = False
            final["decision_confidence"] = min(float(final.get("decision_confidence", 0.5)), 0.2)
            final["what_was_done"] = (str(final.get("what_was_done", "")) +
                                       " Acceptance failed: " + str(acceptance.get("failure", "unknown")))[:1000]
        else:
            final.setdefault("context_for_memory", {})["acceptance_status"] = "proven"
            # The contract checker saw the verified tool evidence. A noisy
            # FINAL step that died on model prose (a free model emitting
            # <tool_call> XML instead of the handoff JSON — observed live)
            # must not veto a goal whose acceptance passed: the step record
            # stays truthfully failed (no rewrite, per the honesty rule) but
            # complete_task may let the acceptance contract carry the goal.
            final.setdefault("context_for_memory", {})["acceptance_success"] = True

    def _build_summary(self, turn_id: str, goal_text: str, asteroid, handoffs,
                       meta: UniverseMeta, acceptance: dict[str, Any] | None = None) -> dict[str, Any]:
        in_t, out_t = self.llm.session_tokens()
        artifacts: list[dict[str, Any]] = []
        for h in handoffs:
            artifacts.extend(h.get("artifacts_created") or [])
        return {
            "turn_id": turn_id,
            "goal_text": goal_text,
            "classification": meta.classification,
            "success": asteroid.task_success,
            "gravity_score": round(asteroid.gravity_score, 4),
            "gravity_bucket": _bucket(asteroid.gravity_score),
            "steps": [{"agent": h.get("agent"), "what": h.get("what_was_done", "")[:120],
                       # a step whose own text starts with "failed:" is a
                       # failure no matter what any later phase claimed
                       "success": bool(h.get("task_success"))
                                  and not str(h.get("what_was_done", "")).startswith("failed:"),
                       "tools_used": h.get("tools_used", [])} for h in handoffs],
            "artifacts": artifacts,
            "promoted_to_l3": bool(asteroid.promoted_to),
            "asteroid_id": asteroid.id,
            "llm_calls": len(self.llm.call_log()),
            "input_tokens": in_t,
            "output_tokens": out_t,
            "elapsed_ms": sum(h.get("elapsed_ms", 0) for h in handoffs),
            "acceptance": acceptance or {"success": bool(asteroid.task_success)},
        }

    # ---- chat removed — ZCode: every message is a turn (no_tool_calls is conversation)
    async def chat(self, text: str, **kwargs):
        """Compat shim: routes chat through run_turn (no_tool_calls is conversation)."""
        res = await self.run_turn(text, **kwargs)
        return str(res.get("reply", ""))

    # ---- helpers ----------------------------------------------------------
    async def _maybe_call(self, fn, arg) -> None:
        import inspect
        if inspect.iscoroutinefunction(fn):
            await fn(arg)
        else:
            fn(arg)

    def is_goal_active(self) -> bool:
        return bool(self._active_turns)

    def is_task_active(self) -> bool:
        return self.is_goal_active()


def _bucket(g: float) -> str:
    # thresholds live in ONE place: config.GRAVITY_THRESHOLDS (the old
    # hand-typed 0.30/0.60/0.85 here drifted from the config values)
    from config import GRAVITY_THRESHOLDS
    if g < GRAVITY_THRESHOLDS["nebula"]:
        return "nebula"
    if g < GRAVITY_THRESHOLDS["asteroid"]:
        return "asteroid"
    if g < GRAVITY_THRESHOLDS["planet_to_l3"]:
        return "planet_to_l3"
    return "star_permanent"


_universe_agent: UniverseAgent | None = None


def get_universe_agent() -> UniverseAgent:
    global _universe_agent
    if _universe_agent is None:
        _universe_agent = UniverseAgent()
    return _universe_agent


def reset_universe_agent_for_tests() -> UniverseAgent:
    global _universe_agent
    _universe_agent = UniverseAgent()
    return _universe_agent


# Backward compat aliases (D:/universe/universe-agent/core/agent/orchestrator.py shim)
Orchestrator = UniverseAgent
get_orchestrator = get_universe_agent
reset_orchestrator_for_tests = reset_universe_agent_for_tests
