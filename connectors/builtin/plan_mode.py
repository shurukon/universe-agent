"""connectors/builtin/plan_mode.py — the plan-mode exit door.

plan.submit is the Universe ExitPlanMode twin: while the gate is in plan
mode the agent researches read-only and drafts its plan; calling
plan.submit asks for the user's approval through the EXISTING explicit-
consent path (the consent prompt IS the plan approval), and once
approved the handler flips the gate to full mode so execution can begin.

It deliberately does not re-implement planning — the universe_plan /
acceptance machinery already owns that; this tool is only the mode
switch with an honest answer in every state.
"""
from __future__ import annotations

from typing import Any


def _plan_submit(**_: Any) -> dict[str, Any]:
    """Submit the plan for approval. The explicit-consent prompt that fires
    before this handler runs is the approval itself; reaching the handler
    means the user said yes, so here we only flip the mode.

    The result carries the Universe tool-driven turn stop: planning is done,
    the next action is the USER's (they approve in the composer), so the
    model should not immediately resume tool-calling. `stop_after_result` is
    lifted by the turn engine (core/agent/turn_engine.py) and ends the turn
    with stop_reason="turn_control" after one final no-tools reply."""
    from security.capability import get_gate
    gate = get_gate()
    before = gate.get_permission_mode()
    if before != "plan":
        # honest no-op outside plan mode (full/ask never needed an exit)
        return {"ok": True, "changed": False, "mode": before,
                "note": "not in plan mode — nothing to submit"}
    after = gate.set_permission_mode("ask")
    return {"ok": True, "changed": True, "mode": after,
            "note": "plan approved — full mode resumed",
            "turn_control": {"stop_after_result": True,
                              "reason": "plan submitted for approval"}}


def register(reg) -> None:
    from core.agent.base_agent import Tool
    reg.register(Tool(
        name="ExitPlanMode",
        capability="ExitPlanMode",
        description=(
            "Submit the current plan for user approval and exit plan mode. "
            "The approval prompt is the tool's explicit-consent prompt; "
            "once approved, the session switches back to full mode and "
            "execution may begin. Call this only after presenting the plan."
        ),
        handler=_plan_submit,
        consent="explicit",
        # the two plan-mode doors must be reachable FROM plan mode — without
        # this flag the gate's dynamic allowlist denied the only tool that
        # can leave the mode (audit 2026-11-10)
        allow_in_plan=True,
    ))
