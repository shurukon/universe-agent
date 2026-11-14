"""tests/test_turn_empty_reply.py — A8: an empty model reply used to end
the turn silently.

A response with no text and no tool calls was returned as
stop_reason="no_tool_calls" with text="" — the caller then recorded
success=False with no error, so the user saw a turn that did nothing and
said nothing. The engine now re-asks once (bounded) and closes honestly as
stop_reason="model_empty_reply".
"""
from __future__ import annotations


from tests._engine_harness import FakeLLM, resp, run_turn_fresh


def test_empty_reply_is_re_asked_once_bounded():
    llm = FakeLLM([
        resp(text=""),                      # the empty reply
        resp(text="Here is the answer."),   # the re-ask lands
    ])
    seen: list[tuple[str, dict]] = []
    _r, _events, out = run_turn_fresh(
        llm_client=llm, on_event=lambda k, p: seen.append((k, p)))
    assert out.stop_reason == "no_tool_calls"
    assert out.text == "Here is the answer."
    assert len(llm.calls) == 2, "the engine must re-ask exactly once"
    assert [k for k, _p in seen].count("model_empty_reply") == 1


def test_persistent_empty_reply_closes_honestly():
    """The model keeps answering nothing: the turn must end with
    stop_reason="model_empty_reply", NOT a silent completion."""
    from core.agent.turn_engine import EMPTY_REPLY_MAX_ATTEMPTS
    rounds = [resp(text="") for _ in range(EMPTY_REPLY_MAX_ATTEMPTS + 2)]
    llm = FakeLLM(rounds)
    seen: list[tuple[str, dict]] = []
    _r, _events, out = run_turn_fresh(
        llm_client=llm, on_event=lambda kind, payload: seen.append((kind, payload)))
    assert out.stop_reason == "model_empty_reply"
    assert out.text == ""
    assert len(llm.calls) == EMPTY_REPLY_MAX_ATTEMPTS + 1, (
        "the engine must stop re-asking after the bounded attempts")
    kinds = [k for k, _p in seen]
    assert "model_empty_reply" in kinds, (
        f"the empty-reply attempt must be emitted: {kinds}")
    assert "model_empty_reply_exhausted" in kinds


def test_a_whitespace_only_reply_is_still_empty():
    llm = FakeLLM([
        resp(text="   \n\t  "),        # whitespace is not an answer
        resp(text="real answer"),
    ])
    _r, _events, out = run_turn_fresh(llm_client=llm)
    assert out.text == "real answer"
    assert len(llm.calls) == 2
