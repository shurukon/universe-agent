"""tests/test_session_reflection.py — P4: memory writes are OFF the turn
hot path; the subconscious loop is the ONLY memory writer for session
turns (post-session reflection distills transcripts into L3).

Pins:
  * a session with substantial new content gets ONE L3 session star;
  * the reflection records an idempotent watermark — a second cycle
    distills nothing new;
  * a short session (< 4 new messages) is not distilled;
  * the 'reflected' watermark row never enters the replay;
  * the session turn envelope itself writes ZERO memory (the hot-path
    contract) — the transcript is the only durable state of a turn.
"""
from __future__ import annotations

import asyncio

import pytest


def _seed(store, sid: str, n_turns: int = 3) -> None:
    store.create(id=sid, title="reflect me")
    for i in range(n_turns):
        store.append(sid, {"kind": "message", "role": "user",
                           "content": f"question {i}: how do I deploy the "
                                      f"service on Kubernetes?"})
        store.append(sid, {"kind": "message", "role": "assistant",
                           "content": f"answer {i}: use the helm chart and "
                                      f"roll out gradually."})


def test_reflection_distills_new_content_once(fresh_home):
    from core.session.store import get_session_store
    from core.memory import get_memory
    from core.memory.subconscious.loop import SubconsciousLoop
    store = get_session_store()
    _seed(store, "sess_refl")
    mem = get_memory()
    loop = SubconsciousLoop()
    stats = loop._cycle_body_sync()
    assert stats["reflected"] >= 1
    stars = [s for s in mem.l3.list_stars() if s.domain == "session"]
    assert len(stars) == 1 and "deploy" in stars[0].topic.lower()
    # idempotent: the watermark stops a second distillation of the same rows
    stats2 = loop._cycle_body_sync()
    assert stats2["reflected"] == 0
    stars2 = [s for s in mem.l3.list_stars() if s.domain == "session"]
    assert len(stars2) == 1
    # new activity after the watermark IS distilled again
    store.append("sess_refl", {"kind": "message", "role": "user",
                               "content": "follow-up question one"})
    store.append("sess_refl", {"kind": "message", "role": "assistant",
                               "content": "follow-up answer one"})
    store.append("sess_refl", {"kind": "message", "role": "user",
                               "content": "follow-up question two"})
    store.append("sess_refl", {"kind": "message", "role": "assistant",
                               "content": "follow-up answer two"})
    stats3 = loop._cycle_body_sync()
    assert stats3["reflected"] >= 1


def test_short_session_is_not_distilled(fresh_home):
    from core.session.store import get_session_store
    from core.memory import get_memory
    from core.memory.subconscious.loop import SubconsciousLoop
    store = get_session_store()
    store.create(id="sess_short", title="tiny")
    store.append("sess_short", {"kind": "message", "role": "user",
                                "content": "hi"})
    store.append("sess_short", {"kind": "message", "role": "assistant",
                                "content": "hello"})
    mem = get_memory()
    loop = SubconsciousLoop()
    stats = loop._cycle_body_sync()
    assert stats["reflected"] == 0
    assert not [s for s in mem.l3.list_stars() if s.domain == "session"]


def test_reflected_watermark_never_enters_replay(fresh_home):
    from core.session.store import get_session_store
    store = get_session_store()
    _seed(store, "sess_wm")
    store.append("sess_wm", {"kind": "reflected", "role": "system",
                             "content": "[memory reflection recorded]"})
    msgs = store.history_messages("sess_wm")
    assert all("[memory reflection" not in str(m.get("content"))
               for m in msgs)
    assert any(m.get("role") == "user" for m in msgs)


def test_turn_hot_path_writes_zero_memory(fresh_home, monkeypatch):
    """The session turn's durable state is the transcript alone: running a
    turn creates no planets, no asteroids, no L3 stars."""
    from core.agent.orchestrator import reset_orchestrator_for_tests
    from providers.client import LLMClient, LLMResponse

    async def fake_complete(self, *, agent, messages, max_tokens=1024,
                            temperature=0.0, response_format=None, tools=None,
                            tool_choice=None, tier_hint=None, on_delta=None):
        return LLMResponse(text="a real reply", model="mock",
                           provider="MockLLM", input_tokens=5,
                           output_tokens=3, latency_ms=1,
                           finish_reason="stop", raw={"tool_calls": []},
                           key_alias="pytest")
    monkeypatch.setattr(LLMClient, "complete", fake_complete)
    orch = reset_orchestrator_for_tests()
    summary = asyncio.run(orch.run_turn("remember nothing hot"))
    assert summary["engine"] == "session"
    from core.memory import get_memory
    mem = get_memory()
    assert not [s for s in mem.l3.list_stars() if s.domain == "session"], \
        "the turn must not write memory — reflection owns that"
    assert not mem.l2.list_recent(limit=5), "no asteroids from a session turn"
