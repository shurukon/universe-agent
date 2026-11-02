"""tests/e2e/test_session_e2e.py — end-to-end tests for the session engine.

The session is the unit of work (Claude Code / Codex contract): every user
message is a turn inside a session; the transcript is the only durable
state; resume is a replay, fork is a copy, compaction is a recorded event.
These tests drive the REAL stack — orchestrator, turn engine, session
store, CLI slash handlers, desktop bridge, migration script — with a fake
LLM client so they run offline and fast.
"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest


# ---------------------------------------------------------------- fake LLM
class FakeResp:
    def __init__(self, text: str, calls=None):
        self.text = text
        self.raw = {"tool_calls": calls or []}
        self.finish_reason = "stop"
        self.input_tokens = 10
        self.output_tokens = 5
        self.model = "fake"


class FakeLLM:
    """Minimal LLMClient twin: every call answers plain text."""

    def __init__(self):
        self.n = 0
        self.calls: list[list[str]] = []

    async def complete(self, *, agent, messages, max_tokens=100,
                       response_format=None, tools=None, tool_choice=None,
                       on_delta=None):
        self.n += 1
        self.calls.append([m.get("role") for m in messages])
        return FakeResp(f"reply #{self.n}")

    def call_log(self):
        return [1] * self.n

    def session_tokens(self):
        return (10 * self.n, 5 * self.n)


@pytest.fixture
def orch(fresh_home):
    """Orchestrator with the fake LLM injected (offline session engine)."""
    from core.agent.orchestrator import reset_orchestrator_for_tests
    o = reset_orchestrator_for_tests()
    o.llm = FakeLLM()
    return o


# ------------------------------------------------------------------ engine
def test_session_turn_creates_session_not_goal(orch):
    """The default engine is 'session': a turn creates a session + a
    transcript and ZERO goals rows (the 'goal running' era is over)."""
    summary = asyncio.run(orch.run_turn("hello world"))
    assert summary["engine"] == "session"
    assert summary["session_id"].startswith("sess_")
    assert summary["reply"] == "reply #1"
    from storage.local import get_storage
    row = get_storage().query_one("SELECT COUNT(*) AS c FROM goals")
    assert row["c"] == 0, "session engine must not write goal rows"
    from core.session.store import get_session_store
    ents = get_session_store().transcript(summary["session_id"])
    kinds = [e["kind"] for e in ents]
    assert "turn_start" in kinds and "turn_end" in kinds
    assert ents[-1]["status"] == "completed"


def test_history_replays_across_turns_in_same_session(orch):
    """Second turn in the SAME session sees the first turn's messages —
    full transcript replay, no 24x4000 truncation."""
    s1 = asyncio.run(orch.run_turn("first message"))
    sid = s1["session_id"]
    asyncio.run(orch.run_turn("second message", session_id=sid))
    roles = orch.llm.calls[-1]
    assert roles.count("user") >= 2, "prior turn must be replayed"


def test_session_survives_orchestrator_restart(orch, fresh_home):
    """Persistence: the transcript outlives the process singleton — a
    fresh orchestrator (the 'restart') resumes the session from disk."""
    s1 = asyncio.run(orch.run_turn("persist me"))
    sid = s1["session_id"]
    from core.agent.orchestrator import reset_orchestrator_for_tests
    from core.session.store import get_session_store
    import core.session.store as store_mod
    store_mod._store = None  # simulate process restart of the store singleton
    orch2 = reset_orchestrator_for_tests()
    orch2.llm = FakeLLM()
    s2 = asyncio.run(orch2.run_turn("after restart", session_id=sid))
    assert s2["session_id"] == sid
    ents = get_session_store().transcript(sid)
    texts = " ".join(str(e.get("content", "")) for e in ents)
    assert "persist me" in texts and "after restart" in texts


def test_fork_carries_parent_history(orch):
    """Fork = copy of the transcript + lineage recorded (Codex fork parity)."""
    s1 = asyncio.run(orch.run_turn("parent message"))
    sid = s1["session_id"]
    from core.session.store import get_session_store
    forked = get_session_store().fork(sid)
    assert forked["parent_id"] == sid
    assert forked["id"] != sid
    copied = get_session_store().transcript(forked["id"])
    assert any("parent message" in str(e.get("content", "")) for e in copied)
    # the fork answers as its own session: replay includes the parent turn
    s2 = asyncio.run(orch.run_turn("child message", session_id=forked["id"]))
    assert s2["session_id"] == forked["id"]


def test_compact_drops_pre_compaction_entries(orch):
    """Compaction: entries before the marker are replaced by the summary —
    replay must contain the summary, not the old content."""
    s1 = asyncio.run(orch.run_turn("long forgotten content xyzzy"))
    sid = s1["session_id"]
    from core.session.store import get_session_store
    store = get_session_store()
    store.compact_separator(sid, 500, 40, "everything before was summarized")
    msgs = store.history_messages(sid)
    joined = " ".join(str(m.get("content")) for m in msgs)
    assert "everything before was summarized" in joined
    assert "xyzzy" not in joined


def test_cli_new_fork_compact_sessions_commands(fresh_home):
    """The README-promised trio (/new /fork /compact) plus /sessions are
    registered and work end to end through the slash dispatcher."""
    from cli.slash_commands import register_all, get_command
    from cli.repl import _handle_slash, ConsoleIO
    register_all()
    for name in ("new", "sessions", "fork", "compact"):
        assert get_command(name) is not None, f"/{name} missing"
    io = ConsoleIO()
    asyncio.run(_handle_slash("/new", io))
    assert any("New session:" in o for o in io.outputs())
    asyncio.run(_handle_slash("/sessions", io))
    assert any("session(s)" in o for o in io.outputs())
    asyncio.run(_handle_slash("/fork", io))
    assert any("Forked" in o for o in io.outputs())
    asyncio.run(_handle_slash("/compact", io))
    assert any("empty — nothing to compact" in o or "Compacted" in o
               for o in io.outputs())


def test_goal_engine_still_available_via_flag(orch, monkeypatch):
    """Rollback contract: GAX_ENGINE=goal restores the legacy goal-state
    path (rows in the goals table) — the escape hatch kept open until the
    goal path is deleted after the burn-in period."""
    monkeypatch.setenv("GAX_ENGINE", "goal")
    summary = asyncio.run(orch.run_turn("legacy path"))
    from storage.local import get_storage
    row = get_storage().query_one("SELECT COUNT(*) AS c FROM goals")
    assert row["c"] >= 1, "goal engine must persist a goal row"
    assert summary.get("goal_text") == "legacy path"


# ------------------------------------------------------------------- bridge
def test_bridge_branch_endpoint_forks_session(fresh_home):
    """C2 regression test: /api/turn/{id}/branch must return 200 and fork
    the session — it used to be a guaranteed 500 (goals.session_id never
    existed)."""
    from fastapi.testclient import TestClient
    from config import get_config
    get_config().set("project_root", str(fresh_home))
    from universe_desktop_bridge import app as app_mod
    from observability.events import reset_event_bus_for_tests
    reset_event_bus_for_tests()
    from universe_desktop_bridge.app import reset_runtime_for_tests
    reset_runtime_for_tests()
    with TestClient(app_mod.app) as c:
        # a bridge-style task container row (owner_session_id set correctly)
        from storage.local import get_storage
        st = get_storage()
        st.execute(
            "INSERT INTO goals(id,text,status,created_at,owner_session_id,kind) "
            "VALUES(?,?,?,?,?,?);",
            ("goal-e2e-1", "branch me", "completed", 0.0, "sess_e2e_parent",
             "task"))
        from core.session.store import get_session_store
        store = get_session_store()
        store.create(id="sess_e2e_parent", title="parent")
        store.append("sess_e2e_parent",
                     {"kind": "message", "role": "user", "content": "hi"})
        resp = c.post("/api/turn/goal-e2e-1/branch")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        new_sid = body["session_id"]
        assert new_sid != "sess_e2e_parent"
        copied = store.transcript(new_sid)
        assert any("hi" == str(e.get("content", "")) for e in copied)


# --------------------------------------------------------------- migration
def test_migration_script_backfills_transcripts(fresh_home, monkeypatch):
    """P5: legacy goals rows become session transcripts; the script is
    idempotent (second run skips everything)."""
    from storage.local import get_storage
    st = get_storage()
    st.execute(
        "INSERT INTO goals(id,text,status,created_at,owner_session_id,kind,"
        "final_summary) VALUES(?,?,?,?,?,?,?);",
        ("goal-mig-task", "do the thing", "completed", 1.0, "", "task",
         json.dumps({"reply": "the thing was done"})))
    st.execute(
        "INSERT INTO goals(id,text,status,created_at,owner_session_id,kind) "
        "VALUES(?,?,?,?,?,?);",
        ("turn-mig-msg-1", "a message turn", "completed", 2.0,
         "goal-mig-task", "message"))
    import scripts.migrate_goals_to_sessions as mig
    monkeypatch.setattr(sys, "argv", ["migrate"])
    assert mig.main() == 0
    from core.session.store import get_session_store
    store = get_session_store()
    ents = store.transcript("goal-mig-task")
    contents = [str(e.get("content", "")) for e in ents]
    assert "do the thing" in contents
    assert "the thing was done" in contents
    # idempotent second run
    assert mig.main() == 0
    assert len(store.transcript("goal-mig-task")) == len(ents)
