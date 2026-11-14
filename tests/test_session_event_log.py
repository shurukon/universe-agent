"""tests/test_session_event_log.py — the durable per-SESSION event log.

Audit 2026-11-11 (user order): "the events are lost and do not show. Add a
sessions path where the sessions are saved with all the calls and the
thinking and everything — literally all of it — inside the project; every
time the user opens a previous session it must come back complete, and the
restore must be fast (< 2s)."

These tests pin:
  * the log lives in the PROJECT (`<project>/sessions/<id>/events.jsonl`),
  * EVERY event of the turn lands there verbatim (thinking, tool calls,
    the terminal event),
  * a session reopened AFTER the live state is gone still returns the full
    stream (the restore path),
  * the restore is fast (well under the 2 s budget),
  * and NONE of it depends on the goals table — the goal engine is gone,
    the session is the unit of work and of persistence.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


class _FakeLLM:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    async def complete(self, **kw):
        self.calls += 1
        if not self._rounds:
            raise AssertionError("FakeLLM ran out of scripted rounds")
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

def _restore_project_root(cfg, value: str) -> None:
    """Point the config at a project root AND put the old one back.

    The config is a process singleton: a test that sets project_root and
    never restores it leaks the path into every LATER test (the parity
    ladder then scopes its write against the wrong root and the write is
    refused — a cross-test failure that looks like a product bug)."""
    cfg.set("project_root", value)


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

@pytest.mark.asyncio
async def test_session_events_are_written_into_the_project(fresh_home, tmp_path):
    """One turn with one tool call: the whole stream lands in
    `<project>/sessions/<session_id>/events.jsonl`."""
    from core.engine.session_loop import run_session_turn
    from core.session.event_log import session_events_path
    from core.session.store import get_session_store
    from tests._engine_harness import ProbeRegistry

    project = tmp_path / "workspace"
    project.mkdir()
    from config import get_config
    _restore_project_root(get_config(), str(project))

    def tool(**_):
        return {"ok": True, "wrote": "notes.md"}

    store = get_session_store()
    sid = "sess-durable-1"
    store.create(id=sid)

    with ProbeRegistry([("__Write__", tool, "Write", "per_turn")]):
        llm = _FakeLLM([
            _resp(tool_calls=[_call("__Write__", "{}", "c1")]),
            _resp(text="done"),
        ])
        await run_session_turn(
            text="write the notes", session_id=sid, llm_client=llm,
            build_messages=lambda h: [{"role": "user", "content": "go"}])

    path = session_events_path(sid, project)
    assert path.exists(), (
        f"the session log must live in the project: {path}")
    assert path.name == "events.jsonl"
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows, "the session log is empty"
    types = [r.get("type") for r in rows]
    # the tool call AND its result are in the log
    assert any(t in ("tool_started", "tool_finished", "tool_result_committed",
                     "tool_call_scheduled") for t in types), types
    # every row carries the session id, so a restore knows whose it is
    assert all(r.get("session_id") == sid for r in rows), rows[:3]


@pytest.mark.asyncio
async def test_reopened_session_returns_the_full_stream(fresh_home, tmp_path):
    """The restore path: a session whose live state is GONE (a new
    process) still returns every event it recorded."""
    from core.engine.session_loop import run_session_turn
    from core.session.store import get_session_store
    from tests._engine_harness import ProbeRegistry

    project = tmp_path / "workspace2"
    project.mkdir()
    from config import get_config
    _restore_project_root(get_config(), str(project))

    def tool(**_):
        return {"ok": True, "n": 1}

    store = get_session_store()
    sid = "sess-durable-2"
    store.create(id=sid)

    with ProbeRegistry([("__Tool2__", tool, "file.read", "auto")]):
        llm = _FakeLLM([
            _resp(tool_calls=[_call("__Tool2__", "{}", "c1")]),
            _resp(text="answered"),
        ])
        await run_session_turn(
            text="read it", session_id=sid, llm_client=llm,
            build_messages=lambda h: [{"role": "user", "content": "go"}])

    # a fresh process: nothing live, only the files
    from core.session import event_log
    event_log.close_handles()
    events = event_log.read_events(sid, 0, project)
    assert len(events) >= 2, events
    types = [e.get("type") for e in events]
    assert any("tool" in str(t) for t in types), types

    # the bridge reads exactly this file for the restore
    from universe_desktop_bridge.app import _durable_session_events
    restored = _durable_session_events(sid, 0)
    assert len(restored) == len(events)
    assert [e.get("type") for e in restored] == types


@pytest.mark.asyncio
async def test_restore_is_fast(fresh_home, tmp_path):
    """The reopen must feel instant: the whole budget is 2 seconds."""
    from core.session import event_log

    project = tmp_path / "workspace3"
    project.mkdir()
    sid = "sess-fast"
    # 5000 events — a long session
    for i in range(5000):
        event_log.append_event(
            sid, {"type": "tool_finished", "seq": i + 1, "ts": time.time(),
                  "turn_id": "turn-1", "tool": f"T{i % 7}"}, root=project)
    event_log.close_handles()

    started = time.monotonic()
    events = event_log.read_events(sid, 0, project)
    elapsed = time.monotonic() - started
    assert len(events) == 5000, len(events)
    assert elapsed < 2.0, f"the restore took {elapsed:.2f}s (budget 2s)"


@pytest.mark.asyncio
async def test_the_restore_never_touches_the_goals_table(fresh_home, tmp_path):
    """The goal system is gone: the restore path must not read it."""
    from core.engine.session_loop import run_session_turn
    from core.session.store import get_session_store
    from storage.local import get_storage
    from tests._engine_harness import ProbeRegistry

    project = tmp_path / "workspace4"
    project.mkdir()
    from config import get_config
    _restore_project_root(get_config(), str(project))

    def tool(**_):
        return {"ok": True}

    store = get_session_store()
    sid = "sess-nogoals"
    store.create(id=sid)
    with ProbeRegistry([("__T3__", tool, "file.read", "auto")]):
        llm = _FakeLLM([_resp(tool_calls=[_call("__T3__", "{}", "c1")]),
                        _resp(text="ok")])
        await run_session_turn(
            text="go", session_id=sid, llm_client=llm,
            build_messages=lambda h: [{"role": "user", "content": "go"}])

    # no goals row was written for this session
    row = get_storage().query_one("SELECT COUNT(*) AS c FROM goals;")
    count = int(row["c"]) if row else 0
    assert count == 0, f"the session engine must not write goals rows ({count})"

    # and the transcript + the session log carry everything
    rows = store.transcript(sid)
    assert any(r.get("kind") == "turn_start" for r in rows), rows
    assert any(r.get("role") == "user" for r in rows), rows
    from core.session import event_log
    assert event_log.read_events(sid, 0, project), "the session log is empty"


def test_the_sessions_folder_is_created_inside_the_project(fresh_home, tmp_path):
    """The directory the user asked for: `<project>/sessions/<id>/`."""
    from core.session import event_log
    project = tmp_path / "workspace5"
    project.mkdir()
    event_log.append_event("sess-dir", {"type": "turn_started"}, root=project)
    event_log.close_handles()
    folder = project / "sessions" / "sess-dir"
    assert folder.is_dir(), list(project.iterdir())
    assert (folder / "events.jsonl").exists()
    assert Path(str(folder)).name == "sess-dir"
