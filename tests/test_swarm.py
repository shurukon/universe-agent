"""tests/test_swarm.py — S2 swarm runtime tests.

Budget caps, read-parallel/write-sequential execution, event stamping, the
general agent's merged whitelist, universe_plan capture and steer injection
in the tool loop. No live LLM: llm and mission agents are stubbed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.agent.swarm as swarm
from observability.events import get_event_bus


@pytest.fixture(autouse=True)
def _clean_swarm_state():
    swarm._goal_spawns.clear()
    yield
    swarm._goal_spawns.clear()


def _stub_backend(monkeypatch, fake_agents=None):
    """Stub the runtime singletons swarm.spawn pulls at call time."""
    fake_mem = SimpleNamespace(search_l4=lambda *a, **k: [],
                               active_rules=lambda: [])
    monkeypatch.setattr("core.memory.get_memory", lambda: fake_mem)
    monkeypatch.setattr("providers.client.get_llm_client",
                        lambda: SimpleNamespace())
    monkeypatch.setattr("security.capability.get_gate",
                        lambda: SimpleNamespace())
    if fake_agents is not None:
        monkeypatch.setattr("core.core_agents.agents.get_agent",
                            lambda name: fake_agents[name])


class _FakeAgent:
    """A subagent whose run() records start/end and always succeeds."""

    def __init__(self, delay: float = 0.05):
        self.delay = delay

    async def run(self, *, memory, llm_client, goal_text, classification,
                  handoff, context, capability_gate, turn_id=""):
        EVENTS.append(("start", goal_text))
        await asyncio.sleep(self.delay)
        EVENTS.append(("end", goal_text))
        return SimpleNamespace(
            task_success=True, what_was_done=f"done: {goal_text}",
            key_decisions=[], artifacts_created=[], tools_used=[],
            decision_confidence=0.8,
        )


EVENTS: list = []


def drain_events(sub, turn_id: str) -> dict:
    """Collect the bus events for one goal (next_event is async + times out
    with asyncio.TimeoutError when the queue runs dry)."""
    async def _drain():
        out: dict = {}
        try:
            while True:
                ev = await sub.next_event(0.2)
                if ev.get("turn_id") == turn_id:
                    out.setdefault(ev["type"], ev)
        except asyncio.TimeoutError:
            pass
        return out
    return asyncio.run(_drain())


# ---- registration + whitelists ---------------------------------------------

def test_spawn_tool_registered_and_whitelisted(fresh_home):
    from connectors.builtin import get_registry
    from core.core_agents.agents import ALL_AGENTS
    reg = get_registry()
    tool = reg.get("Agent")
    assert tool is not None, "spawn missing from the builtin registry"
    assert tool.capability == "universe:agents"
    for name, cls in ALL_AGENTS.items():
        agent = cls()
        assert "Agent" in agent.tool_whitelist_names, name


def test_general_inherits_retired_toolsets(fresh_home):
    from core.core_agents.agents import get_agent
    wl = set(get_agent("universe").tool_whitelist_names)
    # one heirloom per retired specialist proves the union landed
    for heirloom in ("Bash", "BrowserNavigate",
                     "DockerRun", "McpCallTool", "WebSearch", "Agent"):
        assert heirloom in wl, heirloom


# ---- budget caps (honest refusals, never exceptions) -----------------------

def test_spawn_refuses_at_depth_cap(monkeypatch):
    _stub_backend(monkeypatch)
    tok = swarm._swarm_depth.set(2)  # default cap = 2
    try:
        res = asyncio.run(swarm.spawn(
            agent="universe", turn_id="g1",
            missions=[{"role": "research", "brief": "b"}]))
    finally:
        swarm._swarm_depth.reset(tok)
    assert res["ok"] is False
    assert "depth" in res["error"]


def test_spawn_refuses_too_many_missions_per_call(monkeypatch):
    _stub_backend(monkeypatch)
    res = asyncio.run(swarm.spawn(
        agent="universe", turn_id="g2",
        missions=[{"role": "research", "brief": f"b{i}"} for i in range(5)]))
    assert res["ok"] is False
    assert "per-call cap" in res["error"]


def test_spawn_refuses_exhausted_goal_budget(monkeypatch):
    _stub_backend(monkeypatch)
    swarm._goal_spawns["g3"] = {"total": 8}  # default max_total = 8
    res = asyncio.run(swarm.spawn(
        agent="universe", turn_id="g3",
        missions=[{"role": "research", "brief": "b"}]))
    assert res["ok"] is False
    assert "total mission budget" in res["error"]


def test_spawn_refuses_retired_or_missing_role(monkeypatch):
    _stub_backend(monkeypatch)
    res = asyncio.run(swarm.spawn(
        agent="universe", turn_id="g4",
        missions=[{"role": "planning", "brief": "b"}]))
    assert res["ok"] is False
    assert "role" in res["error"]
    res2 = asyncio.run(swarm.spawn(
        agent="universe", turn_id="g4",
        missions=[{"role": "research", "brief": ""}]))
    assert res2["ok"] is False


# ---- execution semantics ----------------------------------------------------

def test_workers_run_sequentially(monkeypatch):
    """Post-S1 spawn only accepts the 'worker' role (the retired specialist
    roles refuse honestly), and every worker runs STRICTLY one after the
    other — never overlapping on files. A worker resolves through the
    UNIVERSE agent machinery (get_agent('universe')), not a phantom
    'worker' agent."""
    fake_agents = {r: _FakeAgent(0.05) for r in
                   ("universe", "research", "review", "security", "code",
                    "design")}
    _stub_backend(monkeypatch, fake_agents)

    EVENTS.clear()
    res = asyncio.run(swarm.spawn(
        agent="universe", turn_id="g6",
        missions=[{"role": "worker", "brief": "c1"},
                  {"role": "worker", "brief": "c2"}]))
    assert res["ok"] is True and res["missions"] == 2
    assert EVENTS == [("start", "c1"), ("end", "c1"),
                      ("start", "c2"), ("end", "c2")], \
        "worker missions must run sequentially"
    assert swarm._goal_spawns["g6"]["total"] == 2


def test_spawn_emits_stamped_events(monkeypatch):
    fake_agents = {r: _FakeAgent(0.01) for r in
                   ("research", "review", "security", "code", "design")}
    _stub_backend(monkeypatch, fake_agents)
    bus = get_event_bus()
    sub = bus.subscribe()
    try:
        asyncio.run(swarm.spawn(
            agent="universe", turn_id="g7",
            missions=[{"role": "worker", "brief": "scan the docs"}]))
        kinds = drain_events(sub, "g7")
        assert "subagent_spawned" in kinds, kinds.keys()
        spawned = kinds["subagent_spawned"]
        assert spawned.get("agent") == "worker"  # the only surviving role
        assert spawned.get("parent") == "universe"
        assert spawned.get("depth") == 1
        assert "scan the docs" in (spawned.get("brief") or "")
        assert isinstance(spawned.get("seq"), int)
        assert "subagent_complete" in kinds
    finally:
        sub.stop()


# ---- plan capture (retired) + steer injection in the turn engine --------------

def _fake_llm(text: str):
    return SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(
        text=text, raw={}, latency_ms=1)))


def test_universe_plan_block_is_retired(fresh_home):
    """The ```universe_plan block is no longer parsed: plans are committed
    with TodoWrite (todo_updated events drive the Plans card)."""
    from core.agent.turn_engine import run_turn
    bus = get_event_bus()
    sub = bus.subscribe()
    try:
        llm = _fake_llm(
            '```universe_plan\n{"plan": [{"step": 0, "instruction": "build it"}]}\n```'
            '\n{"task_success": true}')
        asyncio.run(run_turn(
            llm_client=llm, messages=[{"role": "system", "content": "s"}],
            agent="universe", turn_id="g8", capability_gate=None))
        kinds = drain_events(sub, "g8")
        assert "turn_planned" not in kinds, (
            "turn_planned must be retired with the goal era")
    finally:
        sub.stop()


def test_turn_engine_injects_steer_at_step_boundary(fresh_home):
    from core.agent.turn_engine import run_turn
    bus = get_event_bus()
    sub = bus.subscribe()
    try:
        llm = _fake_llm('{"task_success": true}')
        messages = [{"role": "system", "content": "s"}]
        pending = [["no — use method B instead"]]

        def drain():
            return pending.pop(0) if pending else []

        asyncio.run(run_turn(
            llm_client=llm, messages=messages, agent="universe",
            turn_id="g9", capability_gate=None, drain_steer=drain))
        injected = [m for m in messages
                    if "[USER COURSE-CORRECTION]" in str(m.get("content", ""))]
        assert injected, "steer text never reached the LLM messages"
        assert "method B" in injected[0]["content"]
        kinds = drain_events(sub, "g9")
        assert "steer_received" in kinds, "steer_received event missing"
    finally:
        sub.stop()
