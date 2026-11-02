"""tests/test_work_intent_routing.py — ONE engine routes every message.

The old keyword routing this file used to pin is DELETED (2026-10): there
is no `_looks_like_chat`, no `_contains_work_intent`, no chat-branch
fast-path and no prose fake-tool-call detector — native API tool_calls
replaced text dialects. One agent (universe) + the session engine handle
every message. The anti-hallucination guarantees that survive are pinned
here against CURRENT reality:

  1. a work request and a greeting take the SAME session engine — nothing
     downgrades work to a tool-less branch anymore (routing by keyword is
     gone);
  2. the exact production-failure message ("creat the file do not give to
     me on chat and use design agent") now runs ONE honest session turn
     and ships the session envelope — no goal rows, no fabricated
     "success" with zero agents.

Collected by pytest automatically (tests/test_*.py); no direct callers.
"""
from __future__ import annotations

import asyncio

import pytest

from core.agent.orchestrator import Orchestrator, get_orchestrator
from storage.local import get_storage
from test_honesty_fixes import _MockOpenAI, _install_mock_provider


# ---------------------------------------------------------------- mock LLM
@pytest.fixture(scope="module")
def mock_llm():
    """Reuse the shared mock LLM server on :8399 (started by whichever
    module needs it first); otherwise start a fresh one."""
    import time
    import urllib.request

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
    srv.should_exit = True
    time.sleep(0.05)


class TestOneEngineRoutesEverything:
    """Guard 1: routing is no longer per-message — chat and work both run
    the session engine (the deleted keyword router cannot downgrade work)."""

    def test_work_request_and_greeting_take_the_session_engine(
            self, fresh_home, monkeypatch):
        monkeypatch.delenv("GAX_ENGINE", raising=False)  # the DEFAULT engine
        orch = get_orchestrator()
        seen: list[dict] = []

        async def fake_session_turn(self, *, text, **kwargs):
            seen.append({"text": text, **kwargs})
            return {"engine": "session", "sentinel": text}

        monkeypatch.setattr(Orchestrator, "_run_session_turn",
                            fake_session_turn)
        out_chat = asyncio.run(orch.run_turn("hi"))
        out_work = asyncio.run(orch.run_turn(
            "creat the file do not give to me on chat and use design agent"))
        # BOTH messages went to the session engine — there is no other path
        assert [s["text"] for s in seen] == [
            "hi",
            "creat the file do not give to me on chat and use design agent"]
        assert out_chat == {"engine": "session", "sentinel": "hi"}
        assert out_work["engine"] == "session"
        # and no goal row was minted for either (session engine contract)
        assert get_storage().query_one(
            "SELECT COUNT(*) AS c FROM goals;")["c"] == 0


class TestWorkRequestRunsOneHonestTurn:
    """Guard 2: the exact production failure message now ends as ONE real
    turn with an honest session envelope — never a fabricated success."""

    def test_production_failure_message_gets_session_envelope(
            self, mock_llm, fresh_home):
        _install_mock_provider()
        orch = get_orchestrator()
        summary = asyncio.run(orch.run_turn(
            "creat the file do not give to me on chat and use design agent"))
        assert summary.get("engine") == "session"
        assert str(summary.get("session_id", "")).startswith("sess_")
        # the mock answers plain text: the reply is real content, not an
        # invented tool-call dialect shipped as a "success"
        reply = str(summary.get("reply") or "")
        assert reply.strip()
        assert "dots_function_call" not in reply
        steps = list(summary.get("steps", []))
        assert len(steps) == 1 and steps[0].get("agent") == "universe"
        # the turn is durable in the transcript; no goal rows were written
        from core.session.store import get_session_store
        entries = get_session_store().transcript(summary["session_id"])
        assert any(e.get("role") == "user" for e in entries)
        assert any(e.get("role") == "assistant" and str(e.get("content") or "").strip()
                   for e in entries)
        assert get_storage().query_one(
            "SELECT COUNT(*) AS c FROM goals WHERE id=?;",
            (summary["turn_id"],))["c"] == 0
