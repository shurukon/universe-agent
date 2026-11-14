"""tests/test_turn_batch_cancel.py — A9: cancelling a tool used to DISCARD
the whole batch.

`_run_one` re-raised asyncio.CancelledError and asyncio.gather was called
without return_exceptions, so one cancelled member killed the entire turn
AND threw away every sibling result — the transcript recorded that nothing
had run. A cancelled member is now an honest failed row, its call id still
commits, and only a cancellation of the TURN ITSELF propagates.
"""
from __future__ import annotations

import asyncio
import os

from tests._engine_harness import FakeLLM, ProbeRegistry, call, resp, run_turn_fresh


def test_cancelled_tool_is_committed_not_discarded():
    """A tool cancelled mid-flight yields a failed row; the sibling read
    still commits; the model still sees both tool results.

    The cancellation is produced by the real per-call timeout path
    (asyncio.wait_for), which is what actually fires in production when a
    tool outlives its Tool.timeout_s bound."""
    started = asyncio.Event()

    async def faster(**_):
        return {"ok": True, "fast": True}

    async def slow(**_):
        started.set()
        await asyncio.sleep(30)           # far past the per-call timeout
        return {"ok": True}

    with ProbeRegistry([("__Fast__", faster, "file.read", "auto"),
                        ("__Slow__", slow, "file.read", "auto")]):
        llm = FakeLLM([
            resp(tool_calls=[call("__Fast__", "{}", "c1"),
                             call("__Slow__", "{}", "c2")]),
            resp(text="the slow one was cancelled"),
        ])
        os.environ["GAX_TOOL_TIMEOUT_SECONDS"] = "0.3"
        try:
            _r, events, out = run_turn_fresh(llm_client=llm)
        finally:
            os.environ.pop("GAX_TOOL_TIMEOUT_SECONDS", None)
        assert out.stop_reason == "no_tool_calls"
        rows = {e.get("call_id"): e for e in events if e.get("tool")}
        assert set(rows) == {"c1", "c2"}, rows.keys()
        assert rows["c1"]["result"]["ok"] is True
        assert rows["c2"]["result"]["ok"] is False
        assert "cancelled" in str(rows["c2"]["result"].get("error")).lower() or \
               "timed out" in str(rows["c2"]["result"].get("error")).lower()
        # the turn is still alive and the model saw both results
        second = llm.calls[1]["messages"]
        assert [m.get("tool_call_id") for m in second
                if m.get("role") == "tool"] == ["c1", "c2"]


def test_batch_member_exception_becomes_an_honest_failed_row():
    """A tool that raises (not cancels) must not discard its siblings and
    must produce a committed result row for its own call id."""
    def boom(**_):
        raise RuntimeError("handler exploded")

    def fine(**_):
        return {"ok": True}

    with ProbeRegistry([("__Boom__", boom, "file.read", "auto"),
                        ("__Fine__", fine, "file.read", "auto")]):
        llm = FakeLLM([
            resp(tool_calls=[call("__Boom__", "{}", "c1"),
                             call("__Fine__", "{}", "c2")]),
            resp(text="one tool blew up, the other worked"),
        ])
        _r, events, out = run_turn_fresh(llm_client=llm)
        assert out.stop_reason == "no_tool_calls"
        rows = {e.get("call_id"): e for e in events if e.get("tool")}
        assert set(rows) == {"c1", "c2"}
        assert rows["c1"]["result"]["ok"] is False
        assert "handler exploded" in str(rows["c1"]["result"].get("error"))
        assert rows["c2"]["result"]["ok"] is True
