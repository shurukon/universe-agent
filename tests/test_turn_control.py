"""tests/test_turn_control.py — A1: the tool-driven turn stop.

`turn_control` was unreadable code: the engine read `r.get("turn_control")`
from the `_run_one` result dict, whose keys were only
{call_id,name,ok,raw,llm_content} — nothing ever put the key there, so the
Universe tool-driven turn stop could never fire. These tests prove the wire
now works end to end (tool result → engine row → stop_reason) and that a tool
which does NOT ask for a stop leaves the turn running.
"""
from __future__ import annotations

from tests._engine_harness import FakeLLM, ProbeRegistry, call, resp, run_turn_fresh


def test_tool_turn_control_stops_the_turn():
    """The REAL ExitPlanMode tool, from plan mode through the gate, ends the
    turn with stop_reason="turn_control" after one final no-tools reply."""
    from security.capability import reset_gate_for_tests

    gate = reset_gate_for_tests()
    gate.set_auto_grant(True)
    # the exit door must be reachable FROM plan mode (it used to answer
    # blocked_by=plan_mode — a locked room with no door)
    gate.set_permission_mode("plan")
    llm = FakeLLM([
        resp(tool_calls=[call("ExitPlanMode", "{}", "c1")]),
        resp(text="Plan submitted — waiting for your approval."),
    ])
    _r, events, out = run_turn_fresh(llm_client=llm, capability_gate=gate)
    kinds = [e.get("tool") for e in events]
    assert "ExitPlanMode" in kinds
    assert gate.get_permission_mode() == "ask", "the door flips the mode"
    assert out.stop_reason == "turn_control"
    assert out.text.strip() == "Plan submitted — waiting for your approval."
    assert out.tool_calls_total == 1


def test_ask_user_does_not_stop_the_turn():
    """AskUserQuestion is an interactive tool: its result must NOT stop the
    turn (only stop_after_result does)."""
    def _ask(**_):
        return {"ok": True, "asked": True}

    with ProbeRegistry([("__AskProbe__", _ask, "AskUserQuestion", "per_turn")]):
        llm = FakeLLM([
            resp(tool_calls=[call("__AskProbe__", "{}", "c1")]),
            resp(text="I can continue once you answer."),
        ])
        _r, _events, out = run_turn_fresh(llm_client=llm)
        assert out.stop_reason == "no_tool_calls", out.stop_reason
        assert out.tool_calls_total == 1


def test_turn_control_is_lifted_from_the_handler_result():
    """The plumbing unit: the engine must read the stop request out of the
    row `_run_one` builds, whatever nesting the handler used."""
    with ProbeRegistry([("__NestedStop__",
                         lambda **_: {"ok": True, "turn_control": {
                             "stop_after_result": True, "reason": "done"}},
                         "ExitPlanMode", "explicit")]):
        llm = FakeLLM([
            resp(tool_calls=[call("__NestedStop__", "{}", "c1")]),
            resp(text="finished"),
        ])
        _r, events, out = run_turn_fresh(llm_client=llm)
        rows = [e for e in events if e.get("tool") == "__NestedStop__"]
        assert rows, events
        assert out.stop_reason == "turn_control"
