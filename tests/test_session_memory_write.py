"""P5 B2b: a turn that ran tools writes an L2 asteroid ONCE, off the hot
path — through the subconscious writer, not inside the turn."""
from __future__ import annotations

import pytest


class _FakeLLM:
    async def complete(self, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(
            text="", model="m", usage=None, raw={"tool_calls": []},
            input_tokens=1, output_tokens=1, provider="p", latency_ms=1,
            finish_reason="stop")


@pytest.mark.asyncio
async def test_session_turn_writes_an_l2_asteroid(fresh_home):
    """The memory write is submitted (not executed synchronously) when the
    turn used tools — the hot path stays fast, and the writer records the
    turn's real evidence."""
    from core.engine.session_loop import run_session_turn
    from core.session.store import get_session_store
    from tests._engine_harness import FakeLLM, ProbeRegistry, call, resp

    store = get_session_store()
    sid = "sess-memory-write"
    store.create(id=sid)

    def tool(**_):
        return {"ok": True, "wrote": "report.md"}

    with ProbeRegistry([("__W__", tool, "Write", "per_turn")]):
        llm = FakeLLM([
            resp(tool_calls=[call("__W__", "{}", "c1")]),
            resp(text="done"),
        ])
        summary = await run_session_turn(
            text="write the report", session_id=sid, llm_client=llm,
            build_messages=lambda h: [{"role": "user", "content": "x"}])
    assert summary["tools_used"] == ["__W__"]
    rows = store.transcript(sid)
    tool_rows = [r for r in rows if r.get("role") == "tool"]
    assert len(tool_rows) == 1, rows
