"""core/agent/swarm.py — stripped (2026-10-31): single universe agent only.

Historical swarm of research/code/review/security/design has been removed
(user standing order — only `universe` remains). The `Agent` tool still
exists for `worker` (unspecialized, shares universe toolset) to preserve
prompt compatibility; all other roles now refuse honestly.
"""
from __future__ import annotations

import asyncio
import contextvars
from typing import Any

from observability.events import emit

# depth of the CURRENTLY EXECUTING agent context (0 = the top-level agent)
_swarm_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "universe_swarm_depth", default=0)

# per-goal spawn totals: turn_id -> {"total": int}
_goal_spawns: dict[str, dict[str, int]] = {}

# agent mailboxes for the SendMessage tool: (turn_id, agent) -> [messages].
# Each entry: {"from": str, "message": str, "ts": float}. In-memory, cleared
# with the goal's spawn bookkeeping — same discipline as everything here.
_mailboxes: dict[tuple[str, str], list[dict[str, Any]]] = {}


def deliver_message(to: str = "", message: str = "", turn_id: str = "",
                    from_agent: str = "") -> dict[str, Any]:
    """SendMessage handler: queue a message for a live agent run.

    An agent is 'live' while its goal has spawn bookkeeping (a swarm run is
    in flight); otherwise the message bounces with an honest error instead
    of being silently dropped into a void.
    """
    if not turn_id or turn_id not in _goal_spawns:
        return {"ok": False, "error":
                f"no live run for agent {to!r} in this goal — nothing delivered"}
    key = (turn_id, (to or "universe").strip())
    entry = {"from": (from_agent or "universe").strip(), "message": message,
             "ts": __import__("time").time()}
    _mailboxes.setdefault(key, []).append(entry)
    emit("agent_message", turn_id=turn_id, agent=to,
         from_agent=entry["from"], queued=len(_mailboxes[key]))
    return {"ok": True, "delivered_to": key[1], "queued": len(_mailboxes[key])}


def drain_mailbox(turn_id: str, agent: str) -> list[dict[str, Any]]:
    """Read + clear an agent's queued messages (the runner polls this)."""
    return _mailboxes.pop((turn_id, agent), [])


def clear(turn_id: str) -> None:
    """Goal ended: drop its spawn bookkeeping AND its mailboxes.

    SendMessage is write-only until an agent drains its mailbox — without
    this, deliveries to an agent that never read them accumulated forever
    (engine-fix round). drain_mailbox stays the reader's entry point; the
    orchestrator's goal cleanup calls clear() for every ended goal.
    """
    _goal_spawns.pop(turn_id, None)
    for key in [k for k in _mailboxes if k[0] == turn_id]:
        _mailboxes.pop(key, None)


def _budgets() -> dict[str, int]:
    from config import get_config
    cfg = get_config()
    def _int(key: str, default: int) -> int:
        try:
            return int(cfg.get(key, default) or default)
        except (TypeError, ValueError):
            return default
    return {
        "max_depth": _int("swarm_max_depth", 2),
        "max_children": _int("swarm_max_children_per_call", 4),
        "max_total": _int("swarm_max_total_per_goal", 8),
        "timeout_s": _int("swarm_timeout_s", 600),
    }


async def spawn(agent: str = "", turn_id: str = "", missions: list | None = None) -> dict:
    """Tool handler: run 1..N specialist missions and return their results.

    `agent` (the spawner) and `turn_id` are auto-filled by the registry,
    exactly like skill.activate. Budget breaches return honest errors the
    calling LLM can adapt to — never exceptions.
    """
    from core.core_agents.agents import ALL_AGENTS
    from core.memory import get_memory
    from providers.client import get_llm_client
    from security.capability import get_gate

    b = _budgets()
    depth = _swarm_depth.get()
    if not turn_id:
        return {"ok": False, "error": "spawn: missing goal context"}
    if not missions or not isinstance(missions, list):
        return {"ok": False,
                "error": "spawn: missions must be a non-empty list of "
                         "{role, brief, deliverable} objects"}
    if len(missions) > b["max_children"]:
        return {"ok": False,
                "error": f"spawn refused: {len(missions)} missions exceeds the "
                         f"per-call cap of {b['max_children']}. Split across calls."}
    if depth >= b["max_depth"]:
        return {"ok": False,
                "error": f"spawn refused: swarm depth {depth} is at the cap of "
                         f"{b['max_depth']}. Do this part yourself instead."}

    state = _goal_spawns.setdefault(turn_id, {"total": 0})
    room = b["max_total"] - state["total"]
    if room <= 0:
        return {"ok": False,
                "error": f"spawn refused: the goal's total mission budget of "
                         f"{b['max_total']} is spent. Finish with what you have."}

    # Only worker remains — all specialists purged (single universe agent).
    roles = ["worker"]
    clean: list[dict[str, Any]] = []
    skipped_budget: list[dict[str, Any]] = []
    for index, m in enumerate(missions):
        if not isinstance(m, dict):
            continue
        if index >= room:
            # beyond the per-goal budget — never run. The old slice
            # (`missions[:room]`) dropped these SILENTLY while reporting
            # ok=True: the model never learned its mission was skipped.
            # An honest per-mission entry flows back instead (engine-fix
            # round).
            skipped_budget.append({
                "role": str(m.get("role", "")).strip().lower() or "worker",
                "success": False,
                "what_was_done": "skipped: budget — the goal's per-goal "
                                 "mission cap was reached; run it in a "
                                 "follow-up call",
                "skipped": "budget",
            })
            continue
        role = str(m.get("role", "")).strip().lower() or "worker"
        brief = str(m.get("brief", "")).strip()
        if role not in roles or not brief:
            return {"ok": False,
                    "error": f"spawn: every mission needs a role (one of "
                             f"{sorted(roles)}) and a concrete brief."}
        clean.append({
            "role": role,
            "brief": brief[:2000],
            "deliverable": str(m.get("deliverable", "")).strip()[:500],
            "constraints": str(m.get("constraints", "")).strip()[:500],
            "acceptance": str(m.get("acceptance", "")).strip()[:500],
        })
    if not clean:
        return {"ok": False, "error": "spawn: no valid missions in the call"}

    memory = get_memory()
    llm = get_llm_client()
    gate = get_gate()

    async def _run_one(mission: dict[str, Any]) -> dict[str, Any]:
        role = mission["role"]
        token = _swarm_depth.set(depth + 1)
        emit("subagent_spawned", turn_id=turn_id, parent=agent,
             agent=role, brief=mission["brief"][:200], depth=depth + 1)
        try:
            from core.core_agents.agents import get_agent
            instruction = mission["brief"]
            if mission["deliverable"]:
                instruction += f"\n\nDeliverable: {mission['deliverable']}"
            if mission["constraints"]:
                instruction += f"\nConstraints: {mission['constraints']}"
            if mission["acceptance"]:
                instruction += f"\nCompletion is proven when: {mission['acceptance']}"
            # Forward parent classification to subagents (review fix)
            try:
                _parent_planet = memory.l1.get_planet_by_goal(turn_id) if hasattr(memory, "l1") else None
                _parent_classification: dict[str, Any] = {}
                if _parent_planet is not None:
                    _parent_classification = getattr(_parent_planet, "session_context", {}).get("classification", {}) or getattr(_parent_planet, "classification", {}) or {}
            except Exception:
                _parent_classification = {}
            context: dict[str, Any] = {
                "mission_instruction": instruction,
                "mission_deliverable": mission["deliverable"],
                "phase": {},
                "l4_skills": [s.to_dict() for s in memory.search_l4(
                    mission["brief"], category="", domain="",
                    # a worker has no skill ownership of its own: it sees
                    # the universe agent's skills (same system by design)
                    target_agent="universe" if role == "worker" else role,
                    top_k=3)],
                "l3_stars": [],
                "active_rules": memory.active_rules(),
                "handoff": None, "classification": _parent_classification, "untrusted": [],
            }
            # the worker IS the universe agent machinery under a neutral
            # label: full toolset, same turn loop, no specialist prompt
            runner = get_agent("universe" if role == "worker" else role)
            pkg = await asyncio.wait_for(
                runner.run(
                    memory=memory, llm_client=llm,
                    goal_text=mission["brief"], classification=context.get("classification", {}),
                    handoff=None, context=context, capability_gate=gate,
                    turn_id=turn_id),
                timeout=b["timeout_s"])
            out = {
                "role": role,
                "success": bool(pkg.task_success),
                "what_was_done": pkg.what_was_done[:400],
                "key_decisions": pkg.key_decisions[:5],
                "artifacts": pkg.artifacts_created[:5],
                "tools_used": pkg.tools_used[:10],
            }
            emit("subagent_complete", turn_id=turn_id, agent=role,
                 success=bool(pkg.task_success),
                 summary=pkg.what_was_done[:200])
            return out
        except asyncio.TimeoutError:
            emit("subagent_complete", turn_id=turn_id, agent=role,
                 success=False, summary=f"timed out after {b['timeout_s']}s")
            return {"role": role, "success": False,
                    "what_was_done": f"mission timed out after {b['timeout_s']}s"}
        except Exception as exc:
            emit("subagent_complete", turn_id=turn_id, agent=role,
                 success=False, summary=str(exc)[:200])
            return {"role": role, "success": False,
                    "what_was_done": f"mission failed: {exc}"[:400]}
        finally:
            _swarm_depth.reset(token)

    # read-only roles run in parallel; writers run sequentially so two
    # writers never touch the same files at once
    READERS = {"research", "review", "security"}
    state["total"] += len(clean)
    results: list[dict[str, Any]] = []
    parallel = [m for m in clean if m["role"] in READERS]
    sequential = [m for m in clean if m["role"] not in READERS]
    if parallel:
        results.extend(await asyncio.gather(*[_run_one(m) for m in parallel]))
    for mission in sequential:
        results.append(await _run_one(mission))
    results.extend(skipped_budget)
    return {"ok": True, "missions": len(results), "results": results,
            "skipped_budget": len(skipped_budget)}


def register(reg: Any) -> None:
    """Register the spawn tool on the builtin registry (called from
    connectors.builtin._register_builtins)."""
    from core.agent.base_agent import Tool
    reg.register(Tool(
        name="Agent", capability="universe:agents",
        description=("Run subagent missions: parallel for read work "
                     "(research/review/security), sequential for writers "
                     "(code/design). Each mission: {role, brief, deliverable, "
                     "constraints, acceptance}. role is one of research/code/"
                     "review/security/design, or 'worker' — an unspecialized "
                     "subagent that shares your full toolset and executes "
                     "whatever mission the brief states (omit role for a "
                     "worker). Spawn only with a reason — you can do most "
                     "work yourself."),
        handler=spawn, consent="auto", resources=["universe:agents"],
    ))
