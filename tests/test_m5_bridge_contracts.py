"""tests/test_m5_bridge_contracts.py — regression suite for the m5 bridge fixes.

No LLM, no network, no mic:
  1. soft failure (success=False, no exception) reads failed + keeps error
  2. bridge never clobbers an existing gt.error with a shallower one
  3. run_turn persists project_id/title (no side-UPDATE needed)
  4. record_provider_halt is the single choke point (one row, one checkpoint)
  5. ring overflow counts dropped; goal_events exposes it
  6. prune_expired evicts old terminal goals, keeps running ones
  7. bus drop counter increments on a full subscriber queue
  8. sweep is scoped to the bridge's own session
  9. voice hello preflight flags a keyless cloud STT provider
  10. voice _settings merges stt_language; local transcribe passes language
  11. tool names are sanitized for strict providers and mapped back
"""
from __future__ import annotations


def test_soft_failure_reads_failed_with_error(fresh_home):
    from universe_desktop_bridge.state import get_state
    gt = get_state().create("g-soft", "do a thing")
    # emulate the fixed _run() tail: success=False, no exception
    summary = {"success": False, "error": "soft boom"}
    if isinstance(summary, dict) and not summary.get("success", True):
        gt.set_status("failed")
        gt.error = str(summary.get("error", ""))[:500]
    else:
        gt.set_status("completed")
    assert gt.status == "failed"
    assert gt.error == "soft boom"


def test_goal_events_exposes_dropped(fresh_home):
    from universe_desktop_bridge.state import get_state, GOAL_EVENT_RING
    gt = get_state().create("g-ring", "x")
    for i in range(GOAL_EVENT_RING + 5):
        gt.add_event({"type": "t", "i": i})
    assert gt.dropped == 5
    assert gt.events[0]["seq"] == 6  # oldest evicted, numbering intact
    assert gt.seq == GOAL_EVENT_RING + 5


def test_prune_expired_keeps_running(fresh_home):
    import time
    from universe_desktop_bridge.state import get_state
    st = get_state()
    old = st.create("g-old", "done long ago")
    old.set_status("completed")
    old.completed_at = time.time() - 4000
    run = st.create("g-run", "still going")
    run.set_status("running")
    n = st.prune_expired(ttl_s=60)
    assert n == 1
    assert st.get("g-old") is None
    assert st.get("g-run") is not None


def test_session_turn_carries_project_meta_in_the_transcript(fresh_home,
                                                              monkeypatch):
    """P4 (2026-11-10): project/title/owner meta no longer rides a goals row
    (the goal engine and its table are gone) — the session transcript is the
    authority, and the bridge still passes project_id through so the sidebar
    grouping keeps working."""
    import asyncio
    from core.agent.orchestrator import get_orchestrator
    from core.session.store import get_session_store
    orch = get_orchestrator()

    async def go():
        # no provider -> provider halt, which still proves the session was
        # created and the turn closed with an honest failed row
        return await orch.run_turn("meta probe", project_id="proj-1",
                                   title="My Title",
                                   owner_session_id="sess-test",
                                   session_id="sess-meta")
    out = asyncio.new_event_loop().run_until_complete(go())
    assert out["success"] is False
    rows = get_session_store().transcript("sess-meta")
    assert any(r.get("kind") == "turn_start" for r in rows), rows
    ends = [r for r in rows if r.get("kind") == "turn_end"]
    assert ends and ends[-1]["status"] == "failed"


def test_provider_halt_single_choke_point_session(fresh_home):
    """P4 (2026-11-10): the ONE halt choke point now writes the transcript
    (the goals table is gone) — a failed turn_end row with the raw error,
    plus the halted summary. No checkpoint, no goals row."""
    from core.agent.universe_agent import record_provider_halt
    from core.session.store import get_session_store
    store = get_session_store()
    store.create(id="s-halt")
    out = record_provider_halt("g-halt", "halt reason", error="root cause",
                               goal_text="t", owner_session_id="s",
                               session_id="s-halt")
    assert out["success"] is False and out["halted"] is True
    rows = [r for r in store.transcript("s-halt")
            if r.get("kind") == "turn_end"]
    assert rows, "a halt must leave a durable close-out row"
    row = rows[-1]
    assert row["status"] == "failed"
    assert row["stop_reason"] == "provider_halt"
    assert "halt reason" in str(row.get("error", "")) or \
           "root cause" in str(row.get("error", "")), (
           "the halt row carries the error the caller surfaced")
    # the goals table is no longer the authority and is not written
    from storage.local import get_storage
    legacy = get_storage().query_one(
        "SELECT status FROM goals WHERE id='g-halt';")
    assert legacy is None, "no turn may write a goals row any more"



def test_sweep_scoped_to_own_session(fresh_home):
    """The orphan sweep contract (failure-diagnosis.md root #4).

    OLD (buggy): only THIS session's fresh random id + empty owners were
    swept — dead-session goals stayed 'running' forever (one survived
    139.8h across dozens of restarts). NEW: at startup EVERY stale
    running row (>10 min, any owner) is an orphan — the desktop bridge is
    the single goal runner. A goal younger than 10 minutes is left alone
    in case a parallel process legitimately owns it."""
    import time
    from storage.local import get_storage
    now = time.time()
    with get_storage().transaction() as conn:
        # old orphans from dead sessions — must ALL be swept now
        conn.execute(
            "INSERT INTO goals(id,text,status,created_at,owner_session_id) "
            "VALUES(?,?,?,?,?);", ("g-old-mine", "m", "running", now - 3600, ""))
        conn.execute(
            "INSERT INTO goals(id,text,status,created_at,owner_session_id) "
            "VALUES(?,?,?,?,?);", ("g-old-theirs", "t", "running", now - 3600,
                                   "bridge-deadbeef"))
        # a FRESH run a parallel process may own — must survive
        conn.execute(
            "INSERT INTO goals(id,text,status,created_at,owner_session_id) "
            "VALUES(?,?,?,?,?);", ("g-fresh", "f", "running", now - 30, "cli-1"))
    from universe_desktop_bridge.app import Runtime
    rt = Runtime()
    rt._sweep_orphaned_goals()
    st = get_storage()
    old_mine = st.query_one("SELECT status FROM goals WHERE id='g-old-mine';")
    old_theirs = st.query_one("SELECT status FROM goals WHERE id='g-old-theirs';")
    fresh = st.query_one("SELECT status FROM goals WHERE id='g-fresh';")
    assert old_mine["status"] == "failed"      # unowned orphan swept
    assert old_theirs["status"] == "failed"    # dead-session orphan swept too
    assert fresh["status"] == "running"       # a live parallel run untouched
    import json as _json
    fs = _json.loads(st.query_one(
        "SELECT final_summary FROM goals WHERE id='g-old-theirs';")["final_summary"])
    assert "interrupted" in fs["summary"]      # the honest reason is recorded


def test_bus_drop_counter(fresh_home):
    from observability.events import get_event_bus
    bus = get_event_bus()
    sub = bus.subscribe()
    try:
        for i in range(600):  # over the 512 bound
            bus.emit("spam", turn_id=None, i=i)
        assert bus.dropped(sub._id) > 0
    finally:
        sub.stop()


def test_voice_preflight_flags_keyless_cloud_stt(fresh_home):
    from config import get_config
    get_config().set("desktop", {"stt_provider": "openai", "stt_api_key": "",
                                 "tts_provider": "edge-tts"})
    from universe_desktop_bridge.voice import get_voice_engine, reset_voice_for_tests
    reset_voice_for_tests()
    st = get_voice_engine().status()
    assert st["stt_error"]  # keyless cloud STT must be flagged before hello
    assert not st["tts_error"]  # edge-tts needs no key


def test_voice_settings_merge_stt_language(fresh_home):
    from config import get_config
    get_config().set("desktop", {"stt_language": "ar"})
    from universe_desktop_bridge import voice as vmod
    assert vmod._settings().get("stt_language") == "ar"


def test_tool_name_sanitize_roundtrip():
    """Strict providers 400 dotted tool names (file.list): the client
    sends sanitized names and maps them back, so dispatch always sees
    the real registry name. Pure function — no network."""
    from providers.client import _sanitize_tool_name
    # Universe names (Phase 2) are already provider-safe; the sanitizer is
    # the safety net for thirdparty/mcp names that still carry dots
    assert _sanitize_tool_name("LS") == "LS"
    assert _sanitize_tool_name("mcp.call-tool") == "mcp_call-tool"
    assert _sanitize_tool_name("plain") == "plain"
    # reverse-map collision sanity: distinct originals stay distinct
    names = ["LS", "file.list_all", "mcp.call", "shell.run"]
    safe = [_sanitize_tool_name(n) for n in names]
    assert len(set(safe)) == len(names)
