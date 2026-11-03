"""tests/test_session_compaction.py — P3: the ONE 85% compute-based
compaction as a logged transcript event.

Pins (the 2026-11 migration decisions):
  * below 85% of the window: nothing compacts, the transcript is untouched;
  * at/above 85%: a model summary is written as ONE synthetic compaction
    marker — replay carries the summary, NOT the old rows (Codex parity);
  * when the model summary fails, the heuristic fallback still records the
    marker and keeps every user message VERBATIM in it;
  * compaction never blocks or breaks the turn itself.
"""
from __future__ import annotations

import asyncio

import pytest


class _SummaryLLM:
    """complete() returns a real summary text (the compaction engine path);
    everything else behaves like the e2e FakeLLM."""

    def __init__(self, fail_summary: bool = False):
        self.fail_summary = fail_summary
        self.n = 0

    async def complete(self, *, agent, messages, max_tokens=100,
                       response_format=None, tools=None, tool_choice=None,
                       on_delta=None, **kw):
        self.n += 1
        if self.fail_summary:
            raise RuntimeError("provider down")

        class _R:
            text = "SUMMARY-OF-EVERYTHING-BEFORE"
            raw = {"tool_calls": []}
            finish_reason = "stop"
            model = "fake"
            input_tokens = 10
            output_tokens = 5
        return _R()

    def call_log(self):
        return [1] * self.n

    def session_tokens(self):
        return (0, 0)


def _seed_big_session(store, sid: str, tokens_worth: int = 30_000) -> None:
    """A session whose replayed history approaches/exceeds the 32k window
    (~4 chars/token): the BULK rides on assistant filler, the user messages
    stay small so their verbatim preservation fits the marker caps."""
    store.create(id=sid, title="big")
    filler_chars = tokens_worth * 4 // 11  # 11 assistant filler messages
    for i in range(12):
        store.append(sid, {"kind": "message", "role": "user",
                           "content": f"msg-{i} please remember thing {i}"
                           + (f" SECRET-CONSTRAINT-{i}" if i == 2 else "")})
        store.append(sid, {"kind": "message", "role": "assistant",
                           "content": f"reply-{i} " + "y" * filler_chars})


def test_below_threshold_no_compaction(fresh_home):
    from core.engine.session_loop import maybe_compact
    from core.session.store import get_session_store
    store = get_session_store()
    store.create(id="sess_small", title="small")
    for i in range(10):
        store.append("sess_small", {"kind": "message", "role": "user",
                                    "content": f"short {i}"})
    assert asyncio.run(maybe_compact(session_id="sess_small",
                                     llm_client=_SummaryLLM())) is False
    assert all(e.get("kind") != "synthetic"
               for e in store.transcript("sess_small"))


def test_at_85_percent_model_summary_replaces_replay(fresh_home):
    from core.engine.session_loop import maybe_compact
    from core.session.store import get_session_store
    store = get_session_store()
    _seed_big_session(store, "sess_big")
    llm = _SummaryLLM()
    assert asyncio.run(maybe_compact(session_id="sess_big",
                                     llm_client=llm)) is True
    assert llm.n == 1, "one compute-based summary call"
    ents = store.transcript("sess_big")
    marker = [e for e in ents if e.get("kind") == "synthetic"]
    assert len(marker) == 1, "exactly ONE compaction marker"
    # replay now carries the summary, not the old rows
    msgs = store.history_messages("sess_big")
    joined = " ".join(str(m.get("content")) for m in msgs)
    assert "SUMMARY-OF-EVERYTHING-BEFORE" in joined
    assert "msg-0 " not in joined.replace("SUMMARY", ""), \
        "pre-compaction bulk must be out of the replay"
    # the marker records the measured budget (Codex preCompact semantics)
    assert marker[0].get("preCompactTokenCount", 0) > 0


def test_model_failure_falls_back_with_verbatim_user_messages(fresh_home):
    from core.engine.session_loop import maybe_compact
    from core.session.store import get_session_store
    store = get_session_store()
    _seed_big_session(store, "sess_fb")
    assert asyncio.run(maybe_compact(
        session_id="sess_fb", llm_client=_SummaryLLM(fail_summary=True))) is True
    msgs = store.history_messages("sess_fb")
    joined = " ".join(str(m.get("content")) for m in msgs)
    assert "SECRET-CONSTRAINT-2" in joined, \
        "the fallback marker must keep user messages verbatim"
    assert "yyyyyyy" not in joined, \
        "the assistant bulk must be out of the replay"


def test_compaction_never_breaks_the_turn(fresh_home):
    """A full session turn still completes when compaction fires mid-flight
    (the P3 pass is wrapped, never a turn blocker)."""
    from core.agent.orchestrator import reset_orchestrator_for_tests
    orch = reset_orchestrator_for_tests()
    orch.llm = _SummaryLLM()
    s1 = asyncio.run(orch.run_turn("hello " + "z" * 140_000))  # > 85% of 32k
    assert s1["success"] is True and s1["engine"] == "session"
    from core.session.store import get_session_store
    ents = get_session_store().transcript(s1["session_id"])
    assert ents[-1].get("status") == "completed"
