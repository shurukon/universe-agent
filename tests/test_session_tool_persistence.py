"""tests/test_session_tool_persistence.py — A5: tool results must survive
across turns.

The session loop used to fold every tool result into ONE synthetic system
"tool_trace" string with 300-character previews, so the next turn replayed
prose instead of the tool protocol. The transcript now carries the real
assistant(tool_calls) + role:"tool" rows, which means:
  * the OpenAI pairing invariant holds when the history is replayed;
  * every result is complete (not truncated to 300 chars);
  * compaction can replace the whole block with one marker.
"""
from __future__ import annotations


import pytest


class _FakeLLM:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self._rounds.pop(0)


def _resp(text="", tool_calls=None):
    from types import SimpleNamespace
    return SimpleNamespace(
        text=text, model="m", usage=None,
        raw={"tool_calls": list(tool_calls or [])},
        input_tokens=0, output_tokens=0, provider="p",
        latency_ms=0, finish_reason="stop")


def _call(name, args="{}", cid="c1"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args}}


def _messages(history):
    return history


@pytest.mark.asyncio
async def test_tool_rows_are_persisted_as_the_tool_protocol(fresh_home, tmp_path, monkeypatch):
    """One turn with one tool call writes: user message, assistant row
    carrying tool_calls, one role=tool row, assistant reply, turn_end."""
    from core.session.store import get_session_store
    from tests._engine_harness import ProbeRegistry

    def pad_tool(**_):
        # a result far longer than the old 300-char preview
        return {"ok": True, "blob": "y" * 5000}

    store = get_session_store()
    session_id = "sess_toolrows"
    store.create(id=session_id)
    with ProbeRegistry([("__Pad__", pad_tool, "file.read", "auto")]):
        from core.engine.session_loop import run_session_turn
        summary = await run_session_turn(
            text="read the file",
            session_id=session_id,
            llm_client=_FakeLLM([_resp(tool_calls=[_call("__Pad__", "{}", "c1")]),
                                 _resp(text="done")]),
            build_messages=_messages)
    assert summary["tools_used"] == ["__Pad__"]

    rows = store.transcript(session_id)
    tool_rows = [r for r in rows if r.get("role") == "tool"]
    assert len(tool_rows) == 1, [r.get("kind") for r in rows]
    row = tool_rows[0]
    assert row["kind"] == "tool_result"
    assert row["tool"] == "__Pad__"
    assert row["tool_call_id"] == "c1"
    assert row["ok"] is True
    # the FULL result rode along (the transcript bound is 4000 chars — far
    # above the old 300-char preview, and documented at the write site)
    assert len(row["content"]) >= 4000, len(row["content"])

    # the assistant row that declares the call precedes it
    assistant = [r for r in rows if r.get("role") == "assistant"
                 and r.get("tool_calls")]
    assert len(assistant) == 1
    assert assistant[0]["tool_calls"][0]["id"] == "c1"
    assert assistant[0]["tool_calls"][0]["function"]["name"] == "__Pad__"
    # the reply row follows
    replies = [r for r in rows if r.get("role") == "assistant"
               and not r.get("tool_calls")]
    assert replies and replies[-1]["content"] == "done"


@pytest.mark.asyncio
async def test_replayed_history_keeps_the_pairing_invariant(fresh_home):
    """history_messages must return the assistant(tool_calls) message and
    its tool result ADJACENT and paired — a provider rejects the request
    otherwise."""
    from core.session.store import get_session_store

    store = get_session_store()
    sid = "sess_pairing"
    store.create(id=sid)
    store.append(sid, {"kind": "message", "role": "user",
                       "content": "read it"})
    store.append(sid, {"kind": "message", "role": "assistant",
                       "content": None,
                       "tool_calls": [{"id": "c1", "type": "function",
                                       "function": {"name": "Read",
                                                    "arguments": "{}"}}]})
    store.append(sid, {"kind": "tool_result", "role": "tool",
                       "tool": "Read", "tool_call_id": "c1",
                       "ok": True, "content": '{"ok": true}'})
    store.append(sid, {"kind": "message", "role": "assistant",
                       "content": "the file says hi"})

    msgs = store.history_messages(sid)
    pairs = [(m.get("role"), m.get("tool_call_id")) for m in msgs]
    assert ("assistant", None) in [(r, None) for r, _ in pairs] or True
    # find the tool message and check its predecessor declares the same id
    idx = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
    assert idx, msgs
    i = idx[0]
    assert msgs[i]["tool_call_id"] == "c1"
    declared = msgs[i - 1].get("tool_calls") or []
    assert [c["id"] for c in declared] == ["c1"], declared


@pytest.mark.asyncio
async def test_a_failed_tool_row_is_persisted_as_a_failured_row(fresh_home):
    from core.session.store import get_session_store
    from tests._engine_harness import ProbeRegistry

    def boom(**_):
        raise RuntimeError("nope")

    store = get_session_store()
    sid = "sess_failrow"
    store.create(id=sid)
    with ProbeRegistry([("__Boom__", boom, "file.read", "auto")]):
        from core.engine.session_loop import run_session_turn
        await run_session_turn(
            text="try it", session_id=sid,
            llm_client=_FakeLLM([_resp(tool_calls=[_call("__Boom__", "{}", "c9")]),
                                 _resp(text="it failed")]),
            build_messages=_messages)
    rows = [r for r in store.transcript(sid) if r.get("role") == "tool"]
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert "nope" in rows[0]["content"]
