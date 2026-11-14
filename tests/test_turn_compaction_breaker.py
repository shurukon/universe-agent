"""tests/test_turn_compaction_breaker.py — A2: the rapid-refill breaker was
dead code.

`CompactTracking.reset()` had no caller, so `tool_turns_since_compact` grew
forever and `check_rapid_refill()` always took the else branch — the breaker
could never trip. The counter also incremented once per scheduler GROUP
instead of once per model step, so one step with three read groups burned
three tool turns on its own. These tests pin the fixed semantics.
"""
from __future__ import annotations



def _read_tool(name: str):
    def handler(**_):
        return {"ok": True, "pad": "x" * 400}
    return handler


def _reset_tracking():
    from core.agent.turn_engine import CompactTracking
    return CompactTracking()


def test_reset_zeroes_the_tool_turn_counter_only():
    """reset() is called after a successful compaction, so the tool-turn
    measurement restarts from there. The refill STREAK must survive it —
    clearing it too would make the breaker untrippable."""
    import io
    src = io.open("core/agent/turn_engine.py", encoding="utf-8").read()
    assert "compact_tracking.reset()" in src, (
        "the breaker's counter must be reset after a successful compaction")
    t = _reset_tracking()
    t.note_tool_turn()
    t.note_tool_turn()
    assert t.tool_turns_since_compact == 2
    t.consecutive_rapid_refills = 1
    t.reset()
    assert t.tool_turns_since_compact == 0
    assert t.consecutive_rapid_refills == 1, (
        "reset() must not clear the refill streak")
    t.clear_refill_streak()
    assert t.consecutive_rapid_refills == 0


def test_note_tool_turn_is_one_model_step_not_one_batch():
    """The scheduler's groups are not tool turns: a step with three read
    groups is ONE tool turn. The engine's call site (not the class) pins
    this, so the class documents the contract and the source proves it."""
    import io
    src = io.open("core/agent/turn_engine.py", encoding="utf-8").read()
    # exactly one call, OUTSIDE the per-group loop (dedented by 8 spaces
    # from the group loop's body)
    assert "\n        compact_tracking.note_tool_turn()\n" in src, (
        "note_tool_turn must be called once per model step, not inside the "
        "scheduler group loop")
    # and it must NOT still sit inside that loop
    assert "\n            compact_tracking.note_tool_turn()\n" not in src


def test_refill_within_the_threshold_trips_the_breaker():
    """Three compactions that re-fill within 3 tool trips each is a broken
    context: the fourth must raise, not loop forever."""
    from core.agent.turn_engine import CompactTracking, RAPID_REFILL_MAX_CONSECUTIVE
    t = CompactTracking()
    tripped = False
    for _ in range(RAPID_REFILL_MAX_CONSECUTIVE + 1):
        try:
            # compaction happens, context refills immediately (1 tool turn)
            t.check_rapid_refill()
            t.note_tool_turn()
            t.reset()          # the engine resets after each compaction
        except RuntimeError:
            tripped = True
            break
    assert tripped, (
        f"the breaker never tripped after {RAPID_REFILL_MAX_CONSECUTIVE} "
        "back-to-back rapid refills")


def test_a_healthy_workload_never_trips_the_breaker():
    """Compaction preceded by MORE than the threshold of tool turns is
    normal work — the breaker must stay silent.

    The order matters: check_rapid_refill() runs at compaction time, so it
    measures the tool turns that happened SINCE the previous compaction
    (which reset() restarted). A healthy run therefore clears the streak."""
    from core.agent.turn_engine import (RAPID_REFILL_TOOL_TURN_THRESHOLD,
                                       CompactTracking)
    t = CompactTracking()
    for _round in range(6):
        for _ in range(RAPID_REFILL_TOOL_TURN_THRESHOLD + 1):
            t.note_tool_turn()
        t.check_rapid_refill()      # healthy: the else branch clears it
        assert t.consecutive_rapid_refills == 0
        t.reset()
