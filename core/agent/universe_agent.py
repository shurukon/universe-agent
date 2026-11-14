"""core/agent/orchestrator.py — the Orchestrator (the one Universe-model agent).

The single mind behind the chat. Every message is a TURN of one
professional agent that owns every tool, every skill and the machine's
memory. The turn runs on the Universe turn engine (core/agent/turn_engine.py —
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
import os
from loguru import logger as log
from typing import Any

from core.agent.base_agent import (
    new_id,
)
from core.memory import UniverseMemory, get_memory
from observability.timeline import record as timeline_record
from providers.client import LLMClient, get_llm_client
from providers.manager import get_provider_manager
from security.capability import get_gate
from storage.local import get_storage

AGENT_TURN_SYSTEM = """You are {name} — a professional agent (the durable-session agent model):
a single mind that owns every tool, every skill, and the memory of this
machine. You talk to the user directly, in {language}.

1. THIS IS ORDINARY AGENT WORK. Treat the message as a normal professional
   request, exactly like a terminal agent treats one: no goal framing, no
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
8. UNIVERSE STUDIO TOOLS. Three real tool surfaces belong to you:
   - StudioAVE — drive the AVE video editor (MCP 127.0.0.1:19789): list its
     catalog, then cut, trim, keyframe, color and export real timelines.
   - StudioDesign — drive the open-design daemon (127.0.0.1:7456) to create
     websites, pages and presentations; read the daemon's own responses.
   - StudioBlender — command Blender through its MCP addon socket
     (127.0.0.1:9876): get_scene_info first, then model, materialize and
     render with execute_code and the addon's command types.
   When a task carries a "[Universe Studio · …]" directive, BEGIN with that
   tool and verify every service response; you may still use every other
   tool around it. When a visual reference helps a Design or 3D task,
   generate one with the Meta image pipeline (the same Meta API key powers
   chat, images and speech — no separate key is ever needed).
"""



# Round-7 parity (reference-harness live annex, adapted to the
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
  {project_root}/.shell/plans/<task>.md, commit the TodoWrite list for the
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
                         owner_session_id: str = "",
                         session_id: str = "") -> dict[str, Any]:
    """ONE choke point for every provider halt (M1, audit 2026-11-10).

    Records the timeline, emits provider_halt + turn_failed on the event
    bus, appends `turn_end status=failed` to the SESSION TRANSCRIPT (the
    only durable record left) and returns the terminal summary dict.

    The legacy signature's goal-era parameters (planet_id, handoffs,
    remaining_plan, owner_session_id) are ACCEPTED AND IGNORED — they
    described state that no longer exists. Nothing writes `goals` rows or
    checkpoints any more; a halt that left no transcript row was invisible
    to the next turn's replay.

    Honesty contract (test_provider_routing_e2e): the user-visible error is
    the RAW provider error — never a re-labeled editorial prelude; the
    failure class rides the event kwargs (kind), next to it, not inside the
    text."""
    timeline_record(turn_id, agent="universe",
                    event="provider_halt", detail=reason)
    try:
        from observability.events import emit
        emit("provider_halt", turn_id=turn_id, reason=reason,
             error=(error or reason)[:300], kind=error_kind)
        emit("turn_failed", turn_id=turn_id, error=reason)
    except Exception:
        pass
    # the durable row lives in the transcript, not the goals table
    try:
        from core.session.store import get_session_store
        store = get_session_store()
        sid = str(session_id or owner_session_id or turn_id)
        if sid:
            if store.get(sid) is None:
                try:
                    store.create(id=sid, title=str(goal_text)[:120])
                except Exception:
                    pass
            store.append(sid, {
                "kind": "turn_end", "turn_id": turn_id,
                "status": "failed", "engine": "session",
                "error": (error or reason)[:500],
                "stop_reason": "provider_halt",
                "error_kind": error_kind})
    except Exception:
        pass  # a halt must still surface to the caller
    return {"turn_id": turn_id, "goal_text": goal_text,
            "session_id": session_id or owner_session_id or turn_id,
            "engine": "session",
            "classification": classification or {}, "success": False,
            "halted": True, "error": reason,
            # the machine-readable failure class rides the summary too, so a
            # caller can branch on it without parsing the raw provider prose
            "error_kind": error_kind,
            "steps": steps or [], "artifacts": [], "elapsed_ms": 0,
            "acceptance": {"success": False, "failure": reason}}

# NOTE: Classification/chat routing removed — Universe happy path: every message is a turn.
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
        """The session-engine turn (the durable-session contract).

        The session transcript is the ONLY durable state: no goals rows,
        no planets, no gravity — the turn is appended to the transcript,
        replayed as context, and closed with a turn_end event. The tool
        loop still runs on the same Universe turn engine and the Capability
        Gate still guards every call. ONE engine: GAX_ENGINE=goal is
        ignored (the goal engine was deleted 2026-11-10, user order).

        This thin binding resolves the session + project root and hands
        off to core.engine.session_loop.run_session_turn — the one and
        only session loop (context assembly, append-only persistence and
        the session-shaped summary envelope live there)."""
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

        # B2c: live memory recall happens ONCE per turn, in the async body,
        # so the prompt builder (a sync callback the session loop calls)
        # only formats. See _recall().
        recalled = await self._recall(str(text))

        def _build(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return self._build_turn_messages(
                goal_text=str(text), language=str(language or "en"),
                history=history,
                l4_skills=recalled["l4_skills"],
                l3_stars=recalled["l3_stars"],
                rules=recalled["active_rules"],
                project_root=effective_root)

        from core.engine.session_loop import run_session_turn
        # PROJECT SCOPING (audit 2026-11-12): the selected project must
        # actually SCOPE the turn. `project_root_ctx` is what the capability
        # gate and the file/shell/search tools read to decide "is this path
        # inside the active project" — and since the goal engine was deleted
        # (2026-11-10) NOTHING set it any more, so every tool was still
        # scoped against the GLOBAL config root while the prompt announced
        # the selected project's root. Selecting a project therefore did
        # nothing: writes inside it were refused as out-of-scope and the
        # agent worked in the wrong directory.
        ctx_token = None
        try:
            from core.agent.project_context import project_root_ctx
            ctx_token = project_root_ctx.set(effective_root or None)
        except Exception:
            ctx_token = None
        try:
            return await run_session_turn(
                text=str(text), session_id=sid, llm_client=self.llm,
                build_messages=_build,
                gate=capability_gate or get_gate(),
                turn_id=turn_id, language=str(language or "en"),
                effort=str(effort or ""), on_step=on_step)
        finally:
            if ctx_token is not None:
                try:
                    from core.agent.project_context import project_root_ctx
                    project_root_ctx.reset(ctx_token)
                except Exception:
                    pass

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
        """Execute one turn with durable state transitions and the Universe
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
        turn_id = turn_id or new_id("turn-")
        # ONE engine (P4, user order 2026-11-10): the legacy goal engine was
        # DELETED — its body, its GAX_ENGINE switch, its `goals` rows, its
        # planets/checkpoints. The session engine is not the default any
        # more, it is the ONLY engine. The env var is still read so an old
        # launcher that sets it gets a loud, honest warning instead of
        # silently running the wrong engine.
        engine_mode = (os.environ.get("GAX_ENGINE", "") or "session").strip().lower()
        if engine_mode == "goal":
            log.warning(
                "GAX_ENGINE=goal is IGNORED: the goal engine was deleted "
                "2026-11-10; the session engine runs this turn")
        return await self._run_session_turn(
            text=text, language=language, turn_id=turn_id,
            on_step=on_step, capability_gate=capability_gate,
            owner_session_id=owner_session_id, project_id=project_id,
            project_root=project_root, session_id=session_id,
            effort=effort)
    # ---- P4: the goal engine was DELETED (user order 2026-11-10) ----------
    # What used to live here: the ~400-line goal body — the `goals` row
    # lifecycle, the per-task L1 planet, the failure/acceptance checkpoints,
    # the acceptance ladder and _load_turn_history. All of it existed only to
    # serve the retired long-horizon mode.
    #
    # The session transcript (core/session/store.py) is now the ONLY durable
    # state: a turn is appended, replayed as context, and closed with a
    # turn_end row. Two things the goal body owned survive, in the session
    # path:
    #   * provider_halt  -> record_provider_halt() above (transcript row +
    #     bus events; no goals row, no checkpoint)
    #   * turn locking    -> core.engine.session_loop.run_session_turn
    # resume_turn now replays the transcript instead of a checkpoint (M5).

    @staticmethod
    def _build_turn_messages(*, goal_text: str, language: str,
                             history: list[dict[str, str]],
                             l4_skills: list[dict[str, Any]],
                             l3_stars: list[dict[str, Any]],
                             rules: list[dict[str, Any]],
                             project_root: str) -> list[dict[str, Any]]:
        """The Universe-style turn prompt: identity + live memory
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


    async def resume_turn(self, turn_id: str, *, on_step: Any = None,
                          capability_gate=None,
                          effort: str | None = None) -> dict[str, Any]:
        """Re-run a turn from its SESSION TRANSCRIPT — the only durable
        record the session engine leaves (audit 2026-11-10, M5).

        The old implementation loaded a checkpoint and then required an L1
        planet for the turn: a planet this engine never creates, so every
        resume raised "planet not found" and the whole path was dead code.
        Resume now re-issues the session's last user message as a fresh turn
        with the transcript (which already carries the prior attempt's
        evidence) as its context. Refusals stay explicit."""
        from core.session.store import get_session_store

        store = get_session_store()
        rows = store.transcript(turn_id)
        user_rows = [r for r in rows if r.get("role") == "user"
                     and r.get("kind", "message") == "message"]
        ends = [r for r in rows if r.get("kind") == "turn_end"]
        if ends and str(ends[-1].get("status") or "") == "completed":
            raise ValueError(f"turn {turn_id} already completed")
        if not user_rows:
            raise ValueError(
                f"no transcript for {turn_id}: nothing to resume "
                "(a resume needs the session's transcript on disk)")
        text = str(user_rows[-1].get("content") or "").strip()
        if not text:
            raise ValueError(f"turn {turn_id} has no user message to resume")
        return await self.run_turn(
            text, turn_id=turn_id, on_step=on_step,
            capability_gate=capability_gate, effort=effort,
            session_id=turn_id)

    @staticmethod
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

    async def chat(self, text: str, **kwargs):
        """Compat shim: routes chat through run_turn (no_tool_calls is conversation)."""
        res = await self.run_turn(text, **kwargs)
        return str(res.get("reply", ""))

    # ---- helpers ----------------------------------------------------------
    def _recall(self, goal_text: str) -> dict[str, Any]:
        """Live memory recall for a turn: the skills index (L4) plus
        remembered knowledge (L3), ranked off the event loop.

        B2c (audit 2026-11-10): the session path passed EMPTY lists into the
        prompt builder, so the five-tier memory contributed nothing to any
        turn — the recall existed only on the deleted goal path. One method,
        called from the prompt builder; L3's top hit is touched so the
        `last_recalled` watermark is real."""
        import asyncio

        async def _async() -> dict[str, Any]:
            def _work() -> dict[str, Any]:
                hits = {
                    "l4_skills": [s.to_dict() for s in self.memory.search_l4(
                        goal_text, top_k=6)],
                    "l3_stars": [s.to_dict() for s in self.memory.search_l3(
                        goal_text, top_k=5)],
                    "active_rules": self.memory.active_rules(),
                }
                if hits["l3_stars"]:
                    try:
                        self.memory.l3.touch(hits["l3_stars"][0]["id"])
                    except Exception:
                        pass
                return hits
            return await asyncio.to_thread(_work)

        try:
            asyncio.get_running_loop()
            return _async()  # caller awaits it
        except RuntimeError:
            # sync context (a worker thread or a CLI caller): run inline
            hits = {
                "l4_skills": [s.to_dict() for s in self.memory.search_l4(
                    goal_text, top_k=6)],
                "l3_stars": [s.to_dict() for s in self.memory.search_l3(
                    goal_text, top_k=5)],
                "active_rules": self.memory.active_rules(),
            }
            if hits["l3_stars"]:
                try:
                    self.memory.l3.touch(hits["l3_stars"][0]["id"])
                except Exception:
                    pass
            return hits

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
