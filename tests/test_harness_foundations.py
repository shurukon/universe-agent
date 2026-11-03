"""tests/test_harness_foundations.py — Phase 1 harness systems.

Covers the shell-parity foundations: todo tools, the hook layer at the
registry chokepoint, the permission-mode matrix (plan blocks / ask asks /
full passes), the per-round context meter, and the ask_user round-trip.
Automations live in tests/test_automations.py.
"""
from __future__ import annotations

import asyncio



# -- todo tools ---------------------------------------------------------------

def test_todo_write_then_read_roundtrip(fresh_home):
    from connectors.builtin import get_registry
    reg = get_registry()

    async def go():
        r = await reg.call("TodoWrite", agent="universe", turn_id="g1", args={
            "todos": [
                {"content": "research", "status": "completed"},
                {"content": "build", "status": "in_progress"},
                {"content": "test", "status": "pending"},
            ]})
        assert r["ok"] is True
        r2 = await reg.call("TodoRead", agent="universe", turn_id="g1", args={})
        assert r2["ok"] is True
        todos = r2["result"]["todos"]
        assert [t["content"] for t in todos] == ["research", "build", "test"]
        assert r2["result"]["in_progress_idx"] == 1

    asyncio.run(go())


def test_todo_write_normalizes_and_single_in_progress(fresh_home):
    from connectors.builtin import get_registry
    reg = get_registry()

    async def go():
        r = await reg.call("TodoWrite", agent="universe", turn_id="g2", args={
            "todos": [
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},  # degrades to pending
                {"content": "x" * 500, "status": "pending"},  # capped at 200
            ]})
        assert r["ok"] is True
        todos = r["result"]["todos"]
        statuses = [t["status"] for t in todos]
        assert statuses.count("in_progress") == 1  # single in_progress rule
        assert all(len(t["content"]) <= 200 for t in todos)
        # strictness: a bogus status rejects the whole write, never silently
        # mutates the model's intent
        r2 = await reg.call("TodoWrite", agent="universe", turn_id="g2", args={
            "todos": [{"content": "bad", "status": "bogus"}]})
        assert r2["ok"] is False
        assert "status" in r2["result"]["error"]

    asyncio.run(go())


def test_todo_read_empty_goal_is_ok(fresh_home):
    from connectors.builtin import get_registry
    reg = get_registry()

    async def go():
        r = await reg.call("TodoRead", agent="universe", turn_id="none", args={})
        assert r["ok"] is True
        assert r["result"]["todos"] == []

    asyncio.run(go())


# -- hook layer at the chokepoint ----------------------------------------------

def test_hook_blocks_first_shell_call_then_passes(fresh_home):
    """The builtin factguard is one-shot per goal: the first shell.exec is
    blocked with blocked_by='hook', the retried call goes through the gate."""
    from connectors.builtin import get_registry
    reg = get_registry()

    async def go():
        args = {"cmd": "echo hello"}
        r1 = await reg.call("Bash", agent="universe", turn_id="gh", args=args)
        assert r1["ok"] is False
        assert r1["blocked_by"] == "hook"
        assert "FACT-FORCING" in r1["error"]
        # the retry (same goal, facts now "presented") reaches the gate
        r2 = await reg.call("Bash", agent="universe", turn_id="gh", args=args)
        assert r2["ok"] is True

    asyncio.run(go())


def test_hook_is_per_goal_not_global(fresh_home):
    from connectors.builtin import get_registry
    reg = get_registry()

    async def go():
        args = {"cmd": "echo hi"}
        await reg.call("Bash", agent="universe", turn_id="ga", args=args)
        r = await reg.call("Bash", agent="universe", turn_id="gb", args=args)
        assert r["blocked_by"] == "hook"  # a NEW goal gets its own one-shot

    asyncio.run(go())


def test_hook_intercepted_event_emitted(fresh_home):
    from connectors.builtin import get_registry
    from observability.events import get_event_bus
    reg = get_registry()
    bus = get_event_bus()

    async def go():
        seen = []
        sub = bus.subscribe()  # async-iterator view; must exist before emit
        with bus.bind_goal("gev"):
            await reg.call("Bash", agent="universe", turn_id="gev",
                           args={"cmd": "echo x"})
        # drain everything queued for this subscriber (next_event RAISES
        # TimeoutError on timeout — that is the end-of-stream signal here)
        while True:
            try:
                ev = await sub.next_event(timeout=0.5)
            except (TimeoutError, asyncio.TimeoutError):
                break
            if ev is None:
                break
            seen.append(ev)
        sub.stop()
        return seen

    seen = asyncio.run(go())
    hits = [e for e in seen if e.get("type") == "hook_intercepted"]
    assert hits and hits[0]["hook_id"] == "builtin-factguard-shell"
    assert hits[0]["action"] == "require_facts"


# -- permission-mode matrix -----------------------------------------------------

def _enforce(tool_name, agent, turn_id, args, gate, reg):
    tool = reg.get(tool_name)
    return asyncio.run(gate.enforce(tool, agent=agent, turn_id=turn_id, args=args))


def test_plan_mode_blocks_write_allows_read(fresh_home):
    from connectors.builtin import get_registry
    from security.capability import get_gate
    reg = get_registry()
    gate = get_gate()
    gate.set_permission_mode("plan")
    r = _enforce("Bash", "universe", "gp", {"cmd": "ls"}, gate, reg)
    assert r["ok"] is False and r["blocked_by"] == "plan_mode"
    r2 = _enforce("Read", "universe", "gp", {"path": "README.md"}, gate, reg)
    assert r2["ok"] is True
    gate.set_permission_mode("full")


def test_edit_mode_lifts_per_goal_to_auto(fresh_home):
    """The Universe ladder: edit lifts per_turn tools (Write) to auto, while
    explicit tools still require the consent path."""
    from connectors.builtin import get_registry
    from security.capability import get_gate
    reg = get_registry()
    gate = get_gate()
    gate.set_auto_grant(False)  # disable the test bypass for this matrix
    gate.set_permission_mode("edit")
    try:
        # yolo-style lift does NOT cover explicit tools
        r = _enforce("Delete", "universe", "gq", {"path": "README.md"}, gate, reg)
        assert r["ok"] is False
        assert r["needs_consent"] is True  # explicit stays explicit in edit
    finally:
        gate.set_permission_mode("full")
        gate.set_auto_grant(True)


def test_full_mode_passes_auto_tools(fresh_home):
    from connectors.builtin import get_registry
    from security.capability import get_gate
    reg = get_registry()
    gate = get_gate()
    gate.set_permission_mode("full")
    r = _enforce("Read", "universe", "gf", {"path": "README.md"}, gate, reg)
    assert r["ok"] is True


def test_plan_submit_flips_plan_to_full(fresh_home):
    from connectors.builtin import get_registry
    from security.capability import get_gate
    reg = get_registry()
    gate = get_gate()
    gate.set_permission_mode("plan")
    r = _enforce("ExitPlanMode", "universe", "gs", {}, gate, reg)
    assert r["ok"] is True
    # Universe ladder: approving a plan lands in ask (the default working mode)
    assert gate.get_permission_mode() == "ask"


def test_invalid_mode_keeps_current(fresh_home):
    from security.capability import get_gate
    gate = get_gate()
    gate.set_permission_mode("plan")
    assert gate.set_permission_mode("bogus") == "plan"


# -- context meter ---------------------------------------------------------------

def test_context_window_heuristic(fresh_home):
    from security.capability import _context_window_for
    assert _context_window_for("claude-opus-5-5")[0] == 200_000
    assert _context_window_for("gpt-5")[0] == 400_000
    assert _context_window_for("gemini-3-pro")[0] == 1_000_000
    used, est = _context_window_for("acme-unknown-9000")
    assert used == 32_000 and est is True


# -- ask_user -------------------------------------------------------------------

def test_ask_user_fallback_without_broker(fresh_home):
    """Outside the bridge the tool must answer honestly, never hang."""
    from connectors.builtin import get_registry
    reg = get_registry()

    async def go():
        r = await reg.call("AskUserQuestion", agent="universe", turn_id="gq2",
                           args={"question": "proceed?",
                                 "options": ["yes", "no"]})
        assert r["ok"] is True
        inner = r["result"]
        assert inner["fallback"] is True
        assert inner["answer"] == "yes"  # first option, flagged as fallback

    asyncio.run(go())


def test_ask_user_requires_question(fresh_home):
    from connectors.builtin import get_registry
    reg = get_registry()

    async def go():
        r = await reg.call("AskUserQuestion", agent="universe", turn_id="gq3",
                           args={"question": "", "options": ["a", "b"]})
        inner = r.get("result") or r
        assert inner["ok"] is False

    asyncio.run(go())


def test_questions_broker_roundtrip(fresh_home):
    from universe_desktop_bridge.questions import QuestionsBroker

    async def go():
        broker = QuestionsBroker()

        async def asker():
            return await broker.ask(question="pick one",
                                    options=["a", "b"], allow_free_text=False)

        task = asyncio.create_task(asker())
        await asyncio.sleep(0.05)  # let the question register
        pend = broker.pending()
        assert len(pend) == 1
        assert pend[0]["question"] == "pick one"
        ok = broker.answer(pend[0]["pending_id"], "b")
        assert ok is True
        result = await asyncio.wait_for(task, 2)
        assert result == {"answer": "b", "timed_out": False}

    asyncio.run(go())


def test_questions_broker_timeout_auto_continues(fresh_home, monkeypatch):
    from universe_desktop_bridge.questions import QuestionsBroker

    async def go():
        broker = QuestionsBroker()
        monkeypatch.setattr(broker, "_timeout_s", lambda: 0.1)
        result = await broker.ask(question="hello?",
                                  options=["first", "second"],
                                  allow_free_text=True)
        assert result["timed_out"] is True
        assert result["answer"] == "first"

    asyncio.run(go())


# -- follow-up queue --------------------------------------------------------------

def test_followup_queue_buffers_while_goal_runs(fresh_home):
    """A message on a task with a RUNNING goal + interaction_behavior=queue
    is buffered, not run concurrently.

    Runtime reset is explicit: this test must not depend on another file
    having already initialized the bridge runtime (a stale _initialized
    skips the orphan sweep — the order-dependent pass/fail flip)."""
    from config import get_config
    get_config().set("desktop", {"interaction_behavior": "queue"})
    import universe_desktop_bridge.app as appmod
    appmod.reset_runtime_for_tests()  # fresh runtime for THIS test

    from fastapi.testclient import TestClient
    # with-context: lifespan initializes the runtime (sweep on the EMPTY
    # db) so the rows below are seeded AFTER it and survive as running
    with TestClient(appmod.app) as client:
        from storage.local import get_storage
        st = get_storage()
        with st.transaction() as conn:
            conn.execute(
                "INSERT INTO goals(id, text, status, kind, title, project_id, "
                "created_at, owner_session_id) VALUES('task-1','t','active',"
                "'task','T','',1.0,'desktop');")
            conn.execute(
                "INSERT INTO goals(id, text, status, kind, title, project_id, "
                "created_at, owner_session_id, parent_goal_id) VALUES('goal-1',"
                "'g','running','goal','','',1.0,'desktop','task-1');")
        r = client.post("/api/turn", json={"text": "next please",
                                           "turn_id": "task-1"},
                        headers={"Authorization": "Bearer dev"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "queued"
        assert appmod._runtime.followup_queues.get("task-1") == ["next please"]
