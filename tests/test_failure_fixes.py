"""tests/test_failure_fixes.py — the Phase 2 root fixes, each tied to a
numbered root in docs/failure-diagnosis.md.

  root #2  empty tool-call names never enter the conversation history
  root #3  the consent deny/timeout message says WHICH and WHY
  root #4  the startup orphan sweep takes stale rows from ANY dead session
  root #5  a skill miss returns actionable suggestions, not a bare error
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest


# ---- root #2: the empty-name guard -------------------------------------------

def test_empty_tool_name_is_dropped_not_replayed():
    """A provider response carrying a nameless tool_call must NEVER reach
    the conversation history — replaying it made strict providers 400
    until the whole goal died ('3 consecutive provider failures')."""
    from providers.client import _parse_tool_calls
    message = SimpleNamespace(tool_calls=[
        SimpleNamespace(id="call-1", type="function",
                        function=SimpleNamespace(name="",
                                                 arguments='{"a": 1}')),
        SimpleNamespace(id="call-2", type="function",
                        function=SimpleNamespace(name="Read",
                                                 arguments='{"path": "x"}')),
        SimpleNamespace(id="call-3", type="function",
                        function=SimpleNamespace(name="   ",
                                                 arguments="{}")),
    ])
    out = _parse_tool_calls(message, reverse_tools={})
    assert [c["function"]["name"] for c in out] == ["Read"]
    assert all(c["id"] for c in out)


def test_sanitize_map_is_still_reversed():
    from providers.client import _parse_tool_calls
    message = SimpleNamespace(tool_calls=[
        SimpleNamespace(id="c", type="function",
                        function=SimpleNamespace(name="file_list",
                                                 arguments="{}")),
    ])
    out = _parse_tool_calls(message, reverse_tools={"file_list": "LS"})
    assert out[0]["function"]["name"] == "LS"


def test_parse_tool_calls_supports_the_streaming_dict_shape():
    """THE root of 'every session fails without executing': the streaming
    path reconstructs tool_calls as plain DICTS; getattr-only parsing read
    their names as '' — historically poisoning the next request with the
    400 'non-empty string' killer, and (post-guard) silently dropping
    EVERY streamed tool call. Both shapes must parse."""
    from providers.client import _parse_tool_calls
    message = SimpleNamespace(tool_calls=[
        {"id": "call-1", "type": "function",
         "function": {"name": "Write", "arguments": '{"path": "x"}'}},
        {"id": "call-2", "type": "function",
         "function": {"name": "", "arguments": "{}"}},   # still dropped
        {"id": "call-3", "type": "function",
         "function": {"name": "   ", "arguments": "{}"}},  # still dropped
    ])
    out = _parse_tool_calls(message, reverse_tools={})
    assert len(out) == 1
    assert out[0]["id"] == "call-1"
    assert out[0]["function"]["name"] == "Write"
    assert out[0]["function"]["arguments"] == '{"path": "x"}'


# ---- root #3: the consent message names the cause ------------------------------

def test_consent_block_message_explains_the_60s_window():
    """The gate's deny result must tell the user the operation waited for
    an approval — 'requires user consent' alone hid 40 silent deaths."""
    import asyncio
    from core.agent.base_agent import Tool
    from security.capability import CapabilityGate

    gate = CapabilityGate.__new__(CapabilityGate)  # bypass __init__ IO
    gate._consent_handler = lambda *a, **k: False  # deny/timeout — same path
    gate._granted = set()
    gate._once_granted = set()
    gate._auto_grant_all = False
    gate._memory = SimpleNamespace(is_blackholed=lambda action: False)

    tool = Tool(name="Write", capability="fs.write", description="",
                handler=lambda **_: {}, consent="per_turn")
    res = asyncio.run(gate._grant_or_block(
        tool=tool, consent="per_turn", agent="code", turn_id="g-1",
        args={"path": "x.txt"}, start=time.time()))
    assert res is not None and res["ok"] is False
    assert "60s" in res["error"]
    assert "Write" in res["error"]
    assert res["needs_consent"] is True


# ---- root #4: the orphan sweep (covered fully in test_m5_bridge_contracts) -----
# test_sweep_scoped_to_own_session asserts the new contract there; this
# repeats the spirit at the app level to catch wiring regressions.

def test_runtime_init_sweeps_stale_running_goals(fresh_home):
    import time as _t
    from storage.local import get_storage
    now = _t.time()
    with get_storage().transaction() as conn:
        conn.execute(
            "INSERT INTO goals(id,text,status,created_at,owner_session_id) "
            "VALUES(?,?,?,?,?);",
            ("g-dead-session", "x", "running", now - 7200, "bridge-old"))
    from universe_desktop_bridge.app import reset_runtime_for_tests, get_runtime
    reset_runtime_for_tests()
    get_runtime().init()  # the sweep runs inside init (lifespan path)
    row = get_storage().query_one(
        "SELECT status, final_summary FROM goals WHERE id='g-dead-session';")
    assert row["status"] == "failed"
    assert "interrupted" in json.loads(row["final_summary"])["summary"]


# ---- root #5: skill miss suggestions -------------------------------------------

@pytest.fixture()
def skill_corpus(fresh_home):
    """A tiny trusted corpus so the suggestion path is deterministic."""
    from core.memory.layers.l4_procedural import L4Procedural, Skill
    l4 = L4Procedural()
    for name, desc in (
        ("react-performance", "React performance profiling and optimization"),
        ("react-testing", "Testing React components and hooks"),
        ("python-packaging", "Packaging and distributing Python projects"),
    ):
        l4.upsert(Skill(name=name, source="test", description=desc,
                        body=desc, tags=["test"], triggers=[name],
                        status="trusted"))
    return l4


def test_skill_miss_returns_suggestions(skill_corpus):
    from connectors.builtin.skills import _suggest
    hits = _suggest("react perfomance")  # (typo'd, like the model's guesses)
    assert hits and any("react" in h.lower() for h in hits)


def test_skill_read_miss_error_carries_suggestions(fresh_home, skill_corpus):
    from connectors.builtin import reset_registry_for_tests
    reg = reset_registry_for_tests()
    tool = reg.get("Skill")
    res = tool.handler(skill_name="no-such-skill-xyz")
    assert res["ok"] is False
    assert "suggestions" in res and res["suggestions"]
    assert "Skill" in res["error"]
