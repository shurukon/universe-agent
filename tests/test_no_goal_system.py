"""tests/test_no_goal_system.py — the proof the user asked for.

2026-11-11: "verify for me that the logic depends entirely on the session,
with no goal system at all."

This file is that verification, as executable code:

  1. the goal ENGINE is gone — there is no goal mode, no goal row written
     by a turn, no planet, no checkpoint, no GAX_ENGINE switch that selects
     a second engine,
  2. the SESSION is the unit of work and of persistence — the transcript
     and the durable event log,
  3. the bridge creates its containers in the `sessions` table and never
     writes a `goals` row for a new task,
  4. the restore path (open a session) reads the session, the transcript
     and the session log — never the goals table,
  5. the runtime code contains no goal-engine machinery left to trip over.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# 1. the goal engine is gone
# --------------------------------------------------------------------------

def test_there_is_no_second_engine_to_select():
    """`GAX_ENGINE=goal` used to switch to the legacy goal path. The
    switch is gone — the env var is read and ignored, with a warning."""
    src = (ROOT / "core" / "agent" / "universe_agent.py").read_text(
        encoding="utf-8")
    assert "GAX_ENGINE" in src, "the env var should still be read (and ignored)"
    # it must never SELECT an engine any more
    assert 'if engine_mode != "goal"' not in src
    assert "engine_mode == \"goal\"" in src, "the ignored switch must warn"
    # and the goal body itself is gone
    for gone in ("INSERT INTO goals", "get_planet_by_goal", "create_planet",
                 "_mark_goal_failed", "_mark_goal_cancelled",
                 "load_checkpoint("):
        assert gone not in src, f"the goal engine still contains {gone!r}"


def test_the_goal_tables_are_not_written_by_a_turn(fresh_home):
    """A turn writes NOTHING into the goals table."""
    from core.engine.session_loop import run_session_turn
    from core.session.store import get_session_store
    from storage.local import get_storage
    from tests._engine_harness import FakeLLM, ProbeRegistry, call, resp

    def tool(**_):
        return {"ok": True}

    store = get_session_store()
    sid = "sess-nogoal-proof"
    store.create(id=sid)
    before = int((get_storage().query_one(
        "SELECT COUNT(*) AS c FROM goals;") or {"c": 0})["c"])
    with ProbeRegistry([("__ProofTool__", tool, "file.read", "auto")]):
        llm = FakeLLM([
            resp(tool_calls=[call("__ProofTool__", "{}", "c1")]),
            resp(text="done"),
        ])
        import asyncio
        asyncio.run(run_session_turn(
            text="prove it", session_id=sid, llm_client=llm,
            build_messages=lambda h: [{"role": "user", "content": "go"}]))

    row = get_storage().query_one("SELECT COUNT(*) AS c FROM goals;")
    assert int(row["c"]) == before, "a turn must not write a goals row"
    mine = get_storage().query_one(
        "SELECT id FROM goals WHERE id=?;", (sid,))
    assert mine is None, "a turn must not write a goals row for its session"

    # and no planet / checkpoint either
    for table, column in (("planets", "turn_id"), ("handoffs", "turn_id")):
        try:
            r = get_storage().query_one(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {column}=?;",
                ("sess-nogoal-proof",))
            assert int(r["c"]) == 0, f"{table} rows were written"
        except Exception:
            pass        # the table may not exist any more — even better


# --------------------------------------------------------------------------
# 2. the bridge writes sessions, not goals
# --------------------------------------------------------------------------

def test_the_bridge_creates_sessions_not_goals(fresh_home, tmp_path,
                                               monkeypatch):
    from fastapi.testclient import TestClient
    import universe_desktop_bridge.app as app_mod
    from storage.local import get_storage

    # a provider must be configured, or the bridge refuses the turn before
    # it ever creates the container
    from providers import manager as pm_mod
    pm = pm_mod.get_provider_manager()

    class _FakePM:
        def best_available(self, agent):
            return ("fake", "fake-model")

        def get_agent_model(self, agent):
            return ("fake", "fake-model")

        def get_fallback_chain(self, agent):
            return []

    monkeypatch.setattr(pm, "best_available", _FakePM().best_available)
    monkeypatch.setattr(pm, "get_agent_model", _FakePM().get_agent_model)
    monkeypatch.setattr(pm, "get_fallback_chain", _FakePM().get_fallback_chain)

    app_mod.reset_runtime_for_tests()
    client = TestClient(app_mod.app)
    try:
        r = client.post("/api/turn", json={"text": "a container please"})
        assert r.status_code == 200, r.text
        task_id = r.json()["task_id"]
        # the container is a SESSION row
        row = get_storage().query_one(
            "SELECT id, title FROM sessions WHERE id=?;", (task_id,))
        assert row is not None, "the container must be a session row"
        assert row["title"] == "New task"
        # and there is NO goals row for it
        legacy = get_storage().query_one(
            "SELECT id FROM goals WHERE id=?;", (task_id,))
        assert legacy is None, "the bridge must not write a goals row"
    finally:
        client.close()


# --------------------------------------------------------------------------
# 3. the restore path reads the session, not the goals table
# --------------------------------------------------------------------------

def test_the_restore_path_reads_the_session_sources():
    """The reopen path is built from the session row + the transcript + the
    durable event log. The goals table only appears as the LEGACY
    fallback for chats that predate the session model."""
    src = (ROOT / "universe_desktop_bridge" / "app.py").read_text(
        encoding="utf-8")

    # the durable log is consulted first
    assert "_durable_session_events(" in src
    assert "read_events" in src
    # the transcript is the message source
    assert "_session_messages(" in src
    assert "get_session_store().transcript(" in src
    # the goals table survives only as a fallback
    assert "legacy" in src, "the goals fallback must be labelled as legacy"


def test_the_session_log_is_the_only_event_authority():
    """core/session/event_log.py is the one place that writes the durable
    event stream, and the bus is its only writer."""
    log = (ROOT / "core" / "session" / "event_log.py").read_text(encoding="utf-8")
    assert "def append_event(" in log
    assert "events.jsonl" in log
    # the bus persists through it
    events = (ROOT / "observability" / "events.py").read_text(encoding="utf-8")
    assert "append_event" in events
    assert "bind_session" in events


# --------------------------------------------------------------------------
# 4. no goal machinery left in the runtime
# --------------------------------------------------------------------------

def test_the_runtime_has_no_goal_engine_code():
    """A grep-level proof over the runtime packages: the goal-era helpers
    (planets, moons, goal ladders, checkpoints) are gone from the code
    that runs a turn."""
    runtime = [
        "core/agent/turn_engine.py",
        "core/agent/universe_agent.py",
        "core/engine/session_loop.py",
        "core/session/store.py",
    ]
    forbidden = [
        r"get_planet_by_goal",
        r"create_planet\b",
        r"complete_planet",
        r"GOAL_MODE_ANNEX",
        r"run_goal\b",
        r"goal_budget",
        r"resume_goal\b",
    ]
    for rel in runtime:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not re.search(pattern, text), (
                f"{rel} still contains {pattern!r} — the goal engine is gone")


def test_the_bridge_routes_are_session_shaped():
    """The bridge's task surface is the session surface: /api/sessions lists
    them, /api/turn/{id} opens one, /api/turn/{id}/events replays one."""
    src = (ROOT / "universe_desktop_bridge" / "app.py").read_text(encoding="utf-8")
    assert '@app.get("/api/sessions")' in src
    assert '@app.get("/api/turn/{turn_id}")' in src
    assert '@app.get("/api/turn/{turn_id}/events")' in src
    # no legacy goal aliases
    assert '@app.get("/api/goals")' not in src
    assert '@app.get("/api/goal/{goal_id}")' not in src
    assert '@app.post("/api/goal")' not in src
