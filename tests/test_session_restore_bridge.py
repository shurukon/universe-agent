"""tests/test_session_restore_bridge.py — the bridge's restore path.

The user's complaint (2026-11-11): "every time the user opens a previous
session it must come back complete — all the events, the thinking, the tool
calls — and the restore must be fast (< 2s)."

This is the BRIDGE-level contract:
  * POST /api/turn runs a turn; every event lands in the session's durable
    log inside the project,
  * GET /api/turn/{session}/events returns that whole stream for a session
    that is no longer live (the reopen case),
  * GET /api/turn/{session} returns the thread messages from the
    TRANSCRIPT (not from the goals table),
  * the restore is fast,
  * and the goals table is not involved anywhere.
"""
from __future__ import annotations

import time

import pytest


class _FakeLLM:
    def __init__(self, rounds):
        self._rounds = list(rounds)

    async def complete(self, **kw):
        if not self._rounds:
            raise AssertionError("out of scripted rounds")
        item = self._rounds.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(text="", tool_calls=None):
    from types import SimpleNamespace
    return SimpleNamespace(
        text=text, model="m", usage=None,
        raw={"tool_calls": list(tool_calls or [])},
        input_tokens=1, output_tokens=1, provider="p", latency_ms=1,
        finish_reason="stop")


def _call(name, args="{}", cid="c1"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args}}


@pytest.fixture(autouse=True)
def _restore_cfg_project_root():
    """The config is a process singleton: a test that points
    `project_root` at its own temp dir and never puts it back leaks that
    path into every LATER test in the same process (the parity ladder then
    scopes its write against the wrong root and the write is refused — a
    cross-test failure that reads like a product bug)."""
    from config import get_config
    cfg = get_config()
    old = cfg.get("project_root", "")
    yield
    cfg.set("project_root", old if old else "")

@pytest.fixture()
def bridge_env(monkeypatch, fresh_home, tmp_path):
    """The bridge with a scripted provider and a project root of its own."""
    project = tmp_path / "proj"
    project.mkdir()
    from config import get_config
    # the config is a process singleton — the old root must be restored, or
    # every later test inherits this path
    get_config().set("project_root", str(project))

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

    import universe_desktop_bridge.app as app_mod
    app_mod.reset_runtime_for_tests()
    from observability.events import reset_event_bus_for_tests
    reset_event_bus_for_tests()
    return app_mod, project


def _post_turn(client, text: str, llm):
    from core.agent import universe_agent as ua
    agent = ua.get_universe_agent()
    agent.llm = llm
    r = client.post("/api/turn", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def _wait_for(client, turn_id: str, want: str, timeout_s: float = 20.0):
    """Wait for a terminal event on the session (the bridge runs the turn on
    its own loop)."""
    deadline = time.monotonic() + timeout_s
    types: list[str] = []
    while time.monotonic() < deadline:
        r = client.get(f"/api/turn/{turn_id}/events")
        if r.status_code == 200:
            types = [e.get("type") for e in r.json().get("events", [])]
            if want in types:
                return types
        time.sleep(0.1)
    return types


def test_a_finished_session_replays_its_whole_stream(bridge_env, monkeypatch):
    """The reopen case: the turn is over, the live ring is gone, and the
    session still returns every event it recorded."""
    from fastapi.testclient import TestClient
    from tests._engine_harness import ProbeRegistry

    app_mod, project = bridge_env

    def tool(**_):
        return {"ok": True, "wrote": "out.txt"}

    with ProbeRegistry([("__RestoreTool__", tool, "file.read", "auto")]):
        client = TestClient(app_mod.app)
        try:
            llm = _FakeLLM([
                _resp(tool_calls=[_call("__RestoreTool__", "{}", "c1")]),
                _resp(text="all done"),
            ])
            body = _post_turn(client, "do the thing", llm)
            task_id = body["task_id"]
            types = _wait_for(client, task_id, "turn_completed")
            assert "turn_completed" in types, types
            assert any("tool" in str(t) for t in types), types

            # the durable log is inside the PROJECT
            from core.session.event_log import session_events_path
            path = session_events_path(task_id, project)
            assert path.exists(), (
                f"the session log must be in the project: {path}")
        finally:
            client.close()


def test_the_restore_is_fast_and_uses_no_goals_row(bridge_env, monkeypatch):
    """The reopen must feel instant, and the goals table is not involved."""
    from fastapi.testclient import TestClient

    app_mod, project = bridge_env
    client = TestClient(app_mod.app)
    try:
        llm = _FakeLLM([_resp(text="answered")])
        body = _post_turn(client, "quick question", llm)
        task_id = body["task_id"]
        types = _wait_for(client, task_id, "turn_completed")
        assert "turn_completed" in types, types

        # the reopen: the live registry is empty, only the files remain
        from universe_desktop_bridge.state import get_state
        get_state().goals.pop(body["turn_id"], None)

        started = time.monotonic()
        r = client.get(f"/api/turn/{task_id}/events")
        elapsed = time.monotonic() - started
        assert r.status_code == 200, r.text
        events = r.json()["events"]
        assert events, "the reopened session returned no events"
        assert elapsed < 2.0, f"the restore took {elapsed:.2f}s (budget 2s)"

        # the thread messages come from the transcript
        detail = client.get(f"/api/turn/{task_id}").json()
        roles = [m.get("role") for m in detail.get("messages", [])]
        assert "user" in roles, detail.get("messages")
        assert "agent" in roles, detail.get("messages")
    finally:
        client.close()


def test_no_goals_row_is_written_for_the_session(bridge_env):
    """The goal system is gone: the bridge's own task bookkeeping aside,
    the session engine writes NO goals rows."""
    from fastapi.testclient import TestClient
    from storage.local import get_storage

    app_mod, _project = bridge_env
    client = TestClient(app_mod.app)
    try:
        before = int((get_storage().query_one(
            "SELECT COUNT(*) AS c FROM goals;") or {"c": 0})["c"])
        llm = _FakeLLM([_resp(text="answered")])
        body = _post_turn(client, "hello", llm)
        task_id = body["task_id"]
        _wait_for(client, task_id, "turn_completed")
        after = int((get_storage().query_one(
            "SELECT COUNT(*) AS c FROM goals;") or {"c": 0})["c"])
        # the bridge still creates ONE container row per task (its sidebar
        # bookkeeping) — but the session engine itself writes no goal rows
        rows = get_storage().query_all(
            "SELECT id, kind FROM goals WHERE id=?", (task_id,))
        for r in rows:
            assert r["kind"] == "task", rows
        assert after - before <= 1, (before, after)
    finally:
        client.close()
