"""tests/test_events.py — the live event bus and its instrumentation points.

Real-behavior tests: a real subscribe/emit round trip, secret redaction at
the boundary, and the full instrumentation chain firing during a real
LLM-driven goal (tool registry -> gate -> skills -> timeline -> memory).
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def bus(fresh_home):
    from observability.events import reset_event_bus_for_tests
    return reset_event_bus_for_tests()


async def test_bus_round_trip_and_stop(bus):
    """emit() fans out to subscribers; stop() ends the stream cleanly."""
    sub = bus.subscribe()
    seen: list[dict] = []

    async def collect() -> None:
        async for ev in sub.events():
            seen.append(ev)

    task = asyncio.ensure_future(collect())
    bus.emit("tool_running", turn_id="g1", agent="code", tool="Read")
    await asyncio.sleep(0.05)
    sub.stop()
    await asyncio.wait_for(task, 2)
    assert len(seen) == 1
    assert seen[0]["type"] == "tool_running"
    assert seen[0]["turn_id"] == "g1"
    assert seen[0]["tool"] == "Read"
    assert seen[0]["ts"] > 0


async def test_bus_drops_oldest_when_subscriber_lags(bus):
    """A stalled UI never blocks the orchestrator: bounded queue, newest wins."""
    sub = bus.subscribe()
    for i in range(700):  # well over the 512 bound
        bus.emit("tool_running", turn_id="g", seq=i)
    collected: list[dict] = []
    q = sub._q
    while not q.empty():
        collected.append(q.get_nowait())
    assert len(collected) == 512
    # the freshest event survived, the oldest were dropped
    seqs = [c.get("seq") for c in collected]
    assert seqs[-1] == 699
    assert seqs[0] > 0
    sub.stop()


async def test_bus_redacts_secrets_at_boundary(bus):
    """Secret-shaped values never leave the process through the bus."""
    sub = bus.subscribe()
    bus.emit("tool_requested", turn_id="g1", tool="HttpClient",
             args={"authorization": "Bearer abcdefghijklmnopqrstuvwxyz123456"})
    ev = await asyncio.wait_for(sub._q.get(), 2)
    assert "[REDACTED]" in str(ev.get("args", {}).get("authorization", ""))
    sub.stop()


async def test_emit_never_raises_on_unserializable_payload(bus):
    """Observability must survive anything the observed code hands it."""
    bus.emit("weird_event", turn_id="g1", blob=object())  # must not raise
    bus.emit("weird_event", turn_id="g1", cycle={})  # fine too


async def test_goal_bind_context_stamps_events(bus):
    """bind_goal stamps emissions from inside the block, even nested ones."""
    with bus.bind_goal("goal-xyz"):
        bus.emit("tool_running", tool="Read")  # no explicit turn_id
    sub = bus.subscribe()
    bus.emit("tool_running", tool="Read")  # outside: None
    ev = await asyncio.wait_for(sub._q.get(), 2)
    assert ev["turn_id"] is None
    sub.stop()


@pytest.mark.llm
async def test_full_instrumentation_chain_during_real_goal(bus, real_provider):
    """A real LLM-driven goal fires every expected event type through the
    registry -> gate -> skill preflight -> timeline -> memory chain."""
    from security.capability import reset_gate_for_tests
    reset_gate_for_tests().set_auto_grant(True)

    sub = bus.subscribe()
    collected: list[dict] = []

    async def collect() -> None:
        async for ev in sub.events():
            collected.append(ev)

    task = asyncio.ensure_future(collect())
    from core.agent.orchestrator import get_orchestrator
    # point project_root at the fresh home so the repo tree stays clean
    from config import get_config
    get_config().set("project_root", str(real_provider))
    summary = await get_orchestrator().run_turn(
        "write a small hello world python function")
    assert summary.get("success") is True
    await asyncio.sleep(0.5)
    sub.stop()
    await asyncio.wait_for(task, 2)

    types = {e["type"] for e in collected}
    # the complete lifecycle reached the bus
    assert "turn_classified" in types
    assert "agent_started" in types
    assert "tool_requested" in types
    assert "tool_running" in types
    assert "tool_finished" in types
    assert "skill_searched" in types
    assert "skill_read" in types
    assert "skill_activated" in types
    assert "llm_round" in types
    assert "memory_updated" in types
    assert "turn_completed" in types
    # skill events carry their galactic coordinates for the UI
    skill_ev = next(e for e in collected if e["type"] == "skill_activated")
    assert skill_ev.get("skill_name")
    assert skill_ev.get("solar_system")
    assert skill_ev.get("orbit")
    # every event carries its goal id
    goal_ids = {e.get("turn_id") for e in collected if e.get("turn_id")}
    assert len(goal_ids) == 1


async def test_blocked_tool_emits_tool_blocked(bus, fresh_home):
    """A gate denial surfaces as tool_blocked (not tool_failed)."""
    from connectors.builtin import get_registry
    from security.capability import reset_gate_for_tests
    reset_gate_for_tests().set_auto_grant(True)
    reg = get_registry()
    sub = bus.subscribe()
    # unknown tool name -> registry failure path
    r = await reg.call("no.such.tool", agent="code", turn_id="g1", args={})
    assert r["ok"] is False
    first = await asyncio.wait_for(sub._q.get(), 2)
    assert first["type"] == "tool_requested"
    ev = await asyncio.wait_for(sub._q.get(), 2)
    assert ev["type"] == "tool_failed"
    assert ev["tool"] == "no.such.tool"
    sub.stop()
