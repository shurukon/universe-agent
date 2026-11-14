"""tests/test_project_scoping.py — selecting a project must SCOPE the turn.

Audit 2026-11-12 (user order): "verify the project-selection problem".

The bug: the capability gate and the file/shell/search tools scope a path
against `project_root_ctx` — a ContextVar the retired goal engine used to
set. When the goal engine was deleted (2026-11-10) nothing replaced it,
so every tool was still scoped against the GLOBAL `config.project_root`
while the prompt announced the SELECTED project's root. Picking a project in
the composer therefore changed the prompt and nothing else: writes inside
the chosen project were refused as out-of-scope, and the agent worked in
the wrong directory.

These tests drive the REAL turn path (UniverseAgent.run_turn with a
project_id) and pin that the tools are scoped against THAT project —
inside it works, outside it is refused.
"""
from __future__ import annotations

import pytest


class _ScriptedLLM:
    """Answers with the scripted rounds; records the messages it saw."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.seen = []

    async def complete(self, **kw):
        self.seen.append(kw.get("messages") or [])
        item = self.rounds.pop(0)
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


@pytest.fixture()
def two_roots(tmp_path, fresh_home):
    """A global root (A) and a separate project root (B)."""
    from config import get_config
    # the builtin fact-forcing hook answers the first write of every turn
    # ("present your facts, then re-issue") — this test measures the SCOPE,
    # so the builtins are disarmed (the hook coverage has its own test file).
    from hooks import get_hook_manager
    hm = get_hook_manager()
    hm._hooks = [h for h in hm._hooks if not h.get("builtin")]
    a = tmp_path / "global"
    b = tmp_path / "chosen"
    a.mkdir()
    b.mkdir()
    (b / "inside.txt").write_text("inside the project", encoding="utf-8")
    (a / "outside.txt").write_text("outside the project", encoding="utf-8")
    cfg = get_config()
    old = cfg.get("project_root", "")
    cfg.set("project_root", str(a))
    try:
        yield a, b
    finally:
        cfg.set("project_root", old if old else "")


def _register_project(root) -> str:
    from storage.local import get_storage
    import time
    pid = "proj-test-scope"
    with get_storage().transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO projects(id, name, root, created_at) "
            "VALUES(?,?,?,?);", (pid, "chosen", str(root), time.time()))
    return pid


@pytest.mark.asyncio
async def test_the_selected_project_scopes_the_tools(two_roots):
    """A file INSIDE the selected project is writable; one OUTSIDE it is
    refused — the gate's scope follows the selection, not the global root."""
    from core.agent.universe_agent import get_universe_agent
    from security.capability import reset_gate_for_tests

    a, b = two_roots
    pid = _register_project(b)
    reset_gate_for_tests().set_auto_grant(True)
    agent = get_universe_agent()

    # 1) write INSIDE the chosen project → allowed
    llm = _ScriptedLLM([
        _resp(tool_calls=[_call("Write", '{"path": "%s", "content": "ok"}'
                                 % str(b / "new.txt").replace("\\", "\\\\"))]),
        _resp(text="written"),
    ])
    agent.llm = llm
    try:
        out = await agent.run_turn("write inside the project",
                                   project_id=pid, session_id="sess-scope-in")
    finally:
        agent.llm = None
    assert (b / "new.txt").read_text(encoding="utf-8") == "ok", out

    # 2) write OUTSIDE it (the global root) → refused
    llm2 = _ScriptedLLM([
        _resp(tool_calls=[_call("Write", '{"path": "%s", "content": "no"}'
                                  % str(a / "nope.txt").replace("\\", "\\\\"))]),
        _resp(text="refused"),
    ])
    agent.llm = llm2
    try:
        await agent.run_turn("write outside the project",
                             project_id=pid, session_id="sess-scope-out")
    finally:
        agent.llm = None
    assert not (a / "nope.txt").exists(), (
        "a write outside the selected project must be refused")


@pytest.mark.asyncio
async def test_the_binding_is_released_after_the_turn(two_roots):
    """The project binding is per TURN: it must not leak into the next task
    (a leak would scope an unrelated turn to the previous project)."""
    from core.agent.project_context import project_root_ctx
    from core.agent.universe_agent import get_universe_agent

    a, b = two_roots
    pid = _register_project(b)
    agent = get_universe_agent()
    agent.llm = _ScriptedLLM([_resp(text="done")])
    try:
        await agent.run_turn("hello", project_id=pid, session_id="sess-scope-2")
    finally:
        agent.llm = None
    assert project_root_ctx.get() in (None, ""), (
        "the project root binding leaked past the turn")


@pytest.mark.asyncio
async def test_the_prompt_announces_the_selected_root(two_roots):
    """The prompt's PROJECT ROOT is the selected project's root (not the
    global one) — the agent is told where it works."""
    from core.agent.universe_agent import get_universe_agent
    agent = get_universe_agent()
    msgs = agent._build_turn_messages(
        goal_text="do the thing", language="en", history=[],
        l4_skills=[], l3_stars=[], rules=[],
        project_root=str(two_roots[1]))
    system = msgs[0]["content"]
    assert "PROJECT ROOT" in system
    assert str(two_roots[1]) in system
    assert str(two_roots[0]) not in system, (
        "the global root must not leak into the prompt")
