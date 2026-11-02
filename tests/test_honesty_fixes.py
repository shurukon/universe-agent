"""The honesty fixes (2026-09 → 2026-10): one honest turn, truthful
acceptance, key visibility, provider sanity.

Covers the flaws found in the user's real runs ("hi" spun 9 agents; every
failed step showed ok=True; "no key for Kilo Gateway" after 7 agents):

  1. a plain chat message goes through run_turn and returns a real reply
     in ONE session turn (the session engine is the default; the keyword
     chat router was DELETED — one agent + one engine handles everything)
  2. acceptance: a run where every handoff failed is NOT a success
  3. acceptance may never rewrite a failed handoff into task_success=True
  4. a dead provider fails fast and HONESTLY (the session engine re-raises
     the provider error after recording the failed turn)
  5. pick_key: the provider's ONLY key is never excluded for error-rate;
     a stale per-agent key assignment falls back instead of returning None
  6. every real LLM call is accounted in the llm_calls table

Uses a real OpenAI-compatible mock server (FastAPI + uvicorn thread) so
the turn itself runs against something.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request

import pytest
import uvicorn

from core.agent.orchestrator import Orchestrator
from core.acceptance import evaluate_goal
from providers.client import LLMError
from providers.manager import get_provider_manager
from storage.local import get_storage


# ------------------------------------------------------------------ mock LLM
class _MockOpenAI:
    """A real HTTP server answering /v1/models and /v1/chat/completions."""

    def __init__(self, port: int = 8399) -> None:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse

        app = FastAPI()

        @app.get("/v1/models")
        def models() -> dict:
            return {"object": "list", "data": [{"id": "mock-1", "object": "model"}]}

        @app.post("/v1/chat/completions")
        def completions(body: dict) -> JSONResponse:
            # the classifier asks for a universe_meta block; anything else
            # gets a plain conversational answer
            content = body.get("messages", [])
            wants_meta = any("UNIVERSE_META" in str(m.get("content", ""))
                             for m in content)
            if wants_meta:
                text = (
                    "```universe_meta "
                    + json.dumps({
                        "mode": "chat", "classification": {
                            "category": "general", "domain": "general",
                            "intent": "chat", "complexity": "low"},
                        "plan_summary": "conversation",
                        "needs_clarification": False,
                        "required_capabilities": [],
                        "acceptance_summary": ["Awaiting a task"],
                    })
                    + "```"
                )
            else:
                text = "Hello! I am Universe — what shall we do today?"
            return JSONResponse({
                "id": "cmpl-mock", "object": "chat.completion",
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": text},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            })

        self._port = port
        self._server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error"))

    def start(self) -> None:
        t = threading.Thread(target=self._server.run, daemon=True)
        t.start()
        for _ in range(50):
            if self._server.started:
                return
            time.sleep(0.1)
        raise RuntimeError("mock LLM server did not start")

    def stop(self) -> None:
        self._server.should_exit = True


@pytest.fixture(scope="module")
def mock_llm():
    """Reuse the mock LLM server when it is already up (the e2e harness
    leaves one running on :8399); otherwise start a fresh one."""
    def _alive(port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models",
                                        timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    if _alive(8399):
        yield None
        return
    srv = _MockOpenAI()
    srv.start()
    yield srv
    srv.stop()


def _install_mock_provider() -> None:
    """Register the mock through the REAL manager path (no shortcut)."""
    pm = get_provider_manager()
    if pm.get_provider("MockLLM") is None:
        pm.add_provider("MockLLM", "http://127.0.0.1:8399/v1")
    from security.secrets_fallback import encrypt_secret
    for k in list(pm.get_provider("MockLLM").keys):
        pm.remove_key("MockLLM", k.id)
    pm.add_key("MockLLM", alias="desktop", encrypted_secret=encrypt_secret("sk-mock-123"))
    from core.core_agents.agents import ALL_AGENTS
    for agent in ALL_AGENTS:
        pm.set_agent_model(agent, "MockLLM", "mock-1")
    pm.set_agent_model("orchestrator", "MockLLM", "mock-1")


# ---------------------------------------------------------------- honest acceptance
def test_all_failed_handoffs_is_not_success():
    handoffs = [
        {"agent": "planning", "task_success": False,
         "what_was_done": "failed: no key for Kilo Gateway", "tools_used": []},
        {"agent": "research", "task_success": False,
         "what_was_done": "failed: no key for Kilo Gateway", "tools_used": []},
    ]
    r = evaluate_goal(goal_text="hi", classification={"category": "general"},
                      handoffs=handoffs)
    assert r["success"] is False
    assert "failed" in r["failure"].lower()


def test_empty_handoffs_is_not_success():
    r = evaluate_goal(goal_text="hi", classification={"category": "general"},
                      handoffs=[])
    assert r["success"] is False


def test_acceptance_never_rewrites_failed_steps():
    handoffs = [
        {"agent": "planning", "task_success": False,
         "what_was_done": "failed: timeout", "tools_used": []},
        {"agent": "code", "task_success": True,
         "what_was_done": "wrote the artifact", "tools_used": ["Write"]},
    ]
    Orchestrator._apply_acceptance(handoffs, {"success": True}, turn_id="g")
    # the failed planning step stays failed — no recovered_by_acceptance lie
    assert handoffs[0]["task_success"] is False
    assert handoffs[1]["task_success"] is True


# ---------------------------------------------------------------- key visibility
def test_sole_key_never_excluded_for_error_rate(fresh_home):
    pm = get_provider_manager()
    if pm.get_provider("SoleTest") is None:
        pm.add_provider("SoleTest", "http://127.0.0.1:9/v1")
    for k in list(pm.get_provider("SoleTest").keys):
        pm.remove_key("SoleTest", k.id)
    pm.add_key("SoleTest", alias="k", encrypted_secret="x")
    key = pm.get_provider("SoleTest").keys[0]
    time.time()
    # a burst of failures: old code skipped the key -> "no key for SoleTest"
    for _ in range(6):
        pm.record_key_error("SoleTest", key.id)
    picked = pm.pick_key("SoleTest", agent="code")
    assert picked is not None, "the ONLY key must never be skipped"


def test_stale_agent_key_assignment_falls_back(fresh_home):
    pm = get_provider_manager()
    if pm.get_provider("StaleTest") is None:
        pm.add_provider("StaleTest", "http://127.0.0.1:9/v1")
    for k in list(pm.get_provider("StaleTest").keys):
        pm.remove_key("StaleTest", k.id)
    pm.add_key("StaleTest", alias="old", encrypted_secret="x")
    old = pm.get_provider("StaleTest").keys[0]
    # agent bound to a key id that no longer exists (replaced at reinstall)
    pm._agent_keys["code"] = ("StaleTest", old.id)
    pm.remove_key("StaleTest", old.id)
    pm.add_key("StaleTest", alias="new", encrypted_secret="x")
    picked = pm.pick_key("StaleTest", agent="code")
    assert picked is not None, "stale assignment must fall back to the pool"


# ---------------------------------------------------------------- live pipeline
def test_hi_gets_a_direct_reply_in_one_session_turn(mock_llm, fresh_home):
    """The user's exact complaint: 'hi' must be answered in ONE agent turn —
    no planning/research/api/code specialists, no skill machinery. Current
    reality (the keyword router was deleted): every message — greeting or
    work — runs the session engine, which returns the session-shaped
    summary and writes ZERO goal rows (the transcript is the record)."""
    import os as _os
    _install_mock_provider()
    orch = Orchestrator()
    summary = asyncio.run(orch.run_turn(
        "hi", turn_id=f"turn-{_os.urandom(4).hex()}"))
    # session-engine envelope: session_id/engine in, gravity keys out
    assert summary.get("engine") == "session"
    assert str(summary.get("session_id", "")).startswith("sess_")
    assert "gravity_score" not in summary
    assert summary.get("conversation") is True
    assert summary.get("success") is True
    reply = str(summary.get("reply") or "")
    assert reply.strip(), "a chat MUST produce a reply text"
    assert len(reply.strip()) >= 3, "the reply must be real text, not empty"
    steps = list(summary.get("steps", []))
    assert len(steps) == 1, f"one general turn, got {len(steps)} steps"
    assert steps[0].get("agent") == "universe", "the universe agent runs the turn"
    assert steps[0].get("tools_used") == [], "a greeting never touches tools"
    # no tool theater: no skill.search/skill.read anywhere in the timeline
    from config import get_config
    tl = ""
    timeline_dir = get_config().timeline_dir
    for path in sorted(timeline_dir.glob(f"{summary['turn_id']}*.jsonl")):
        tl += open(path, encoding="utf-8").read()
    assert "skill.search" not in tl, "a plain chat must not touch the skill index"
    # zero goal rows: the session is the unit of work — the durable record
    # is the transcript, which must carry the user turn AND the reply
    row = get_storage().query_one(
        "SELECT id FROM goals WHERE id=?;", (summary["turn_id"],))
    assert row is None, "the session engine must not write goal rows"
    from core.session.store import get_session_store
    entries = get_session_store().transcript(summary["session_id"])
    assert any(e.get("role") == "user" and e.get("content") == "hi"
               for e in entries), "the user turn must be in the transcript"
    assert any(e.get("role") == "assistant" and str(e.get("content") or "").strip()
               for e in entries), "the reply must be persisted in the transcript"


def test_llm_calls_are_accounted(mock_llm, fresh_home):
    """Every real completion lands in llm_calls — zero rows was the old lie.
    Agent-turn model: one plain message is ONE honest completion round (the
    classify pre-call is gone entirely)."""
    import os as _os
    _install_mock_provider()
    before = get_storage().query_one("SELECT COUNT(*) AS c FROM llm_calls;")["c"]
    orch = Orchestrator()
    summary = asyncio.run(orch.run_turn("hello", turn_id=f"turn-{_os.urandom(4).hex()}"))
    after = get_storage().query_one("SELECT COUNT(*) AS c FROM llm_calls;")["c"]
    assert after == before + 1, "one turn = one accounted completion call"
    assert summary.get("llm_calls") == 1, "the summary must carry the count"


def test_dead_provider_fails_fast_before_any_work(fresh_home):
    """The user's Kilo run: provider reachable-looking but dead. The old
    code burned 9 agents into 'no key'/'timeout' walls. Current reality:
    the session engine raises the provider LLMError straight out of
    run_turn (kind=provider_down) AFTER recording the failed turn in the
    transcript — nothing is burned, nothing fabricated."""
    import os as _os
    from security.secrets_fallback import encrypt_secret
    pm = get_provider_manager()
    pm.add_provider("DeadMock", "http://127.0.0.1:8499/v1")  # nothing listens
    for k in list(pm.get_provider("DeadMock").keys):
        pm.remove_key("DeadMock", k.id)
    pm.add_key("DeadMock", alias="desktop", encrypted_secret=encrypt_secret("sk-dead"))
    from core.core_agents.agents import ALL_AGENTS
    for agent in ALL_AGENTS:
        pm.set_agent_model(agent, "DeadMock", "mock-1")
    pm.set_agent_model("orchestrator", "DeadMock", "mock-1")
    try:
        orch = Orchestrator()
        sid = "sess-dead-provider-probe"
        with pytest.raises(LLMError) as ei:
            asyncio.run(orch.run_turn(
                "write a small python script that renames files by date",
                turn_id=f"turn-halt-{_os.urandom(3).hex()}",
                session_id=sid))
        assert ei.value.kind == "provider_down", str(ei.value)
        # the failure is durable: the transcript carries the failed turn_end
        from core.session.store import get_session_store
        entries = get_session_store().transcript(sid)
        assert any(e.get("kind") == "turn_end" and e.get("status") == "failed"
                   for e in entries), "the failed turn must be recorded"
        assert not any(e.get("kind") == "message" and e.get("role") == "assistant"
                       for e in entries), "no fabricated reply may be persisted"
    finally:
        pm.remove_provider("DeadMock")
