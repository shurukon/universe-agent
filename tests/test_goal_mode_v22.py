"""tests/test_goal_mode_v22.py — v22: the universe agent + the RETIRED goal mode.

The user's standing orders (2026-09 + 2026-10-28): Universe is ONE agent
named "universe" (never "general"); the goal era is OVER — not every task
is a goal, the /goal long-horizon mode is deleted, and ONE turn engine
(core/agent/turn_engine.py, the ZCode loop) runs every task. This file
covers the v22/v25 contract:

- the registry rename (universe in, general gone, model rows migrated),
- NO goal mode: no /goal annex, no goal_mode param, no run_mode column,
  and the engine's operator cap (GAX_TOOL_MAX_ROUNDS) replacing budgets,
- the bridge accepts a legacy mode:"goal" POST as an ordinary task,
- the swarm `worker` role: an unspecialized subagent with the full
  universe toolset,
- no synthetic project row from /api/projects.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from storage.local import get_storage


# ---- the registry rename ------------------------------------------------------

def test_registry_has_universe_not_general(fresh_home):
    from core.core_agents import ALL_AGENTS, get_agent
    assert "universe" in ALL_AGENTS
    assert "general" not in ALL_AGENTS
    a = get_agent("universe")
    assert a.name == "universe"
    assert "universe" in get_agent("universe").system_prompt.lower()


def test_v22_migration_renames_rows_and_primary_project(fresh_home):
    """The v22 upgrade migrates agent_rows in place and re-parents the
    synthetic 'general' project's goals to the oldest REAL project."""
    st = get_storage()
    with st.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO agent_models(agent, provider, model) "
            "VALUES('general','p1','m1');")
        conn.execute(
            "INSERT OR REPLACE INTO projects(id,name,root,created_at,"
            "updated_at) VALUES('proj-real','Real','C:/x',2.0,2.0);")
        conn.execute(
            "INSERT OR REPLACE INTO goals(id, text, status, kind, title, "
            "project_id, created_at, owner_session_id) "
            "VALUES('g-v22','t','active','task','New task','general',"
            "1.0,'x');")

    from schema.v0022_agent_universe_run_mode import upgrade
    upgrade(st)  # idempotent re-run on an already-upgraded schema

    row = st.query_one(
        "SELECT provider, model FROM agent_models WHERE agent='universe';")
    assert row is not None
    assert row["provider"] == "p1" and row["model"] == "m1"
    old = st.query_one(
        "SELECT agent FROM agent_models WHERE agent='general';")
    assert old is None
    g = st.query_one(
        "SELECT project_id FROM goals WHERE id='g-v22';")
    assert g is not None and g["project_id"] == "proj-real"


# ---- the goal mode is GONE (2026-10-28) ----------------------------------------

def test_turn_prompt_has_no_goal_mode_annex(fresh_home):
    from core.agent.orchestrator import Orchestrator
    orch = Orchestrator()
    sys_msg = orch._build_turn_messages(
        goal_text="install the tool", language="en", history=[],
        l4_skills=[], l3_stars=[], rules=[], project_root="")[0]["content"]
    assert "TURN MODE" not in sys_msg
    params = inspect.signature(Orchestrator.run_turn).parameters
    assert "goal_mode" not in params
    from core.agent.orchestrator import GOAL_MODE_ANNEX
    assert GOAL_MODE_ANNEX is None


def test_engine_is_unbounded_operator_cap_replaces_budget(fresh_home, monkeypatch):
    """No per-turn round budget exists; the operator env cap is the only
    step limit and it ends the turn (max_turns_reached → break) with the
    last step's output — no forced finalization, Claude maxTurns semantics."""
    from core.agent.turn_engine import run_turn

    class _ToolLoopLLM:
        """Always requests one (unknown) tool call — the loop would spin
        forever without the operator cap."""

        def __init__(self):
            self.calls = 0

        async def complete(self, **kw):
            self.calls += 1
            return SimpleNamespace(
                text="", raw={"tool_calls": [{
                    "id": f"call-{self.calls}",
                    "type": "function",
                    "function": {"name": "Nope",
                                 "arguments": "{\"x\": 1}"},
                }]}, finish_reason="tool_calls", model="mock-1",
                input_tokens=10, output_tokens=10, latency_ms=1)

    monkeypatch.setenv("GAX_TOOL_MAX_ROUNDS", "2")
    resp, events, out = asyncio.run(run_turn(
        llm_client=_ToolLoopLLM(), messages=[
            {"role": "user", "content": "go"}], agent="universe",
        turn_id="g-cap"))
    assert out.stop_reason == "operator_limit"
    assert out.tool_calls_total == 2  # the cap broke the loop at 2 steps


def test_provider_exception_propagates(fresh_home, monkeypatch):
    """A non-malformed provider failure is re-raised unchanged — the
    orchestrator turns it into a provider halt; the engine never swallows."""
    from core.agent.turn_engine import run_turn

    class _DeadLLM:
        async def complete(self, **kw):
            raise RuntimeError("provider dead")

    with pytest.raises(RuntimeError, match="provider dead"):
        asyncio.run(run_turn(llm_client=_DeadLLM(), messages=[
            {"role": "user", "content": "go"}], agent="universe",
            turn_id="g-dead"))


# ---- the bridge: a legacy mode:"goal" POST is an ordinary task -----------------

def _install_mock_provider() -> None:
    """Register a real (pointing-at-nowhere) provider so the bridge's
    provider preflight passes; the goal row itself never needs the LLM."""
    from providers.manager import get_provider_manager
    pm = get_provider_manager()
    if pm.get_provider("MockLLM") is None:
        pm.add_provider("MockLLM", "http://127.0.0.1:8399/v1")
    from security.secrets_fallback import encrypt_secret
    for k in list(pm.get_provider("MockLLM").keys):
        pm.remove_key("MockLLM", k.id)
    pm.add_key("MockLLM", alias="desktop",
               encrypted_secret=encrypt_secret("sk-mock-123"))
    from core.core_agents.agents import ALL_AGENTS
    for agent in ALL_AGENTS:
        pm.set_agent_model(agent, "MockLLM", "mock-1")
    pm.set_agent_model("orchestrator", "MockLLM", "mock-1")


@pytest.fixture
def client(fresh_home):
    from config import get_config
    get_config().set("project_root", str(fresh_home))
    from universe_desktop_bridge import app as app_mod
    from observability.events import reset_event_bus_for_tests
    reset_event_bus_for_tests()
    app_mod.reset_runtime_for_tests()
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.reset_runtime_for_tests()


def _post_goal(c, **kw):
    return c.post("/api/turn", json={"text": kw.pop("text"),
                                     "language": "en", **kw},
                  headers={"Authorization": "Bearer dev"})


def test_legacy_mode_goal_post_is_an_ordinary_task(client):
    _install_mock_provider()
    r = _post_goal(client, text="watch the window for an hour", mode="goal")
    assert r.status_code == 200, r.text
    cols = [c["name"] for c in
            get_storage().query_all("PRAGMA table_info(goals);")]
    assert "run_mode" not in cols, "the goal run_mode column is retired (v25)"


def test_no_run_mode_column_at_all(client):
    _install_mock_provider()
    r = _post_goal(client, text="summarize the readme")
    assert r.status_code == 200, r.text
    cols = [c["name"] for c in
            get_storage().query_all("PRAGMA table_info(goals);")]
    assert "run_mode" not in cols


def test_projects_list_has_no_synthetic_general(client):
    out = client.get("/api/projects").json()["projects"]
    assert not any(p["id"] == "general" for p in out)


# ---- the swarm worker -----------------------------------------------------------

def test_worker_role_runs_with_universe_toolset(monkeypatch):
    """role omitted / 'worker' = an unspecialized mission run by the universe
    agent machinery under a neutral label."""
    import core.agent.swarm as swarm
    from observability.events import get_event_bus

    swarm._goal_spawns.clear()
    busses = get_event_bus()
    sub = busses.subscribe()
    try:
        fake_mem = SimpleNamespace(
            search_l4=lambda *a, **k: [], active_rules=lambda: [])

        class _UniverseLike:
            name = "universe"

            async def run(self, **kw):
                return SimpleNamespace(
                    task_success=True, what_was_done="worker did it",
                    key_decisions=[], artifacts_created=[], tools_used=[],
                    decision_confidence=0.8)

        seen: dict = {}
        monkeypatch.setattr("core.memory.get_memory", lambda: fake_mem)
        monkeypatch.setattr("providers.client.get_llm_client",
                            lambda: SimpleNamespace())
        monkeypatch.setattr("security.capability.get_gate",
                            lambda: SimpleNamespace())
        monkeypatch.setattr("core.core_agents.agents.get_agent",
                            lambda n: seen.setdefault(n, _UniverseLike())
                            if n == "universe" else _UniverseLike())
        out = asyncio.run(swarm.spawn(
            agent="universe", turn_id="g-worker",
            missions=[{"role": "worker", "brief": "paint the fence"},
                      {"brief": "the unrole default"}]))
        assert out.get("ok") is True, out
        assert out["results"][0]["role"] == "worker"
        assert out["results"][1]["role"] == "worker"
        # the runner resolved through the universe machinery (one fake each)
        assert "universe" in seen
        # skills for a worker resolve against the universe ownership, not a
        # phantom 'worker' owner
        # (search_l4 stub: absence of a crash is the contract here)
    finally:
        sub.stop()
        swarm._goal_spawns.clear()
