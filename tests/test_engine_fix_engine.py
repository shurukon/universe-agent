"""tests/test_engine_fix_engine.py — engine-fix round: agent loop, swarm
and hook fixes.

Covers:
  * the per-call tool timeout (a hung handler can't freeze the turn)
  * the duplicate-call cap + the polling-tools exemption
  * the malformed→max_tokens one-shot fallback
  * the effort-chip token map (single source in base_agent)
  * a tool-verified turn reaches the 0.45 idle-promote gravity threshold
  * spawn reports budget-skipped missions honestly; goal clear() drops mailboxes
  * pre_shell hooks cover Bash/PowerShell/CodeExecute; clear_goal_state
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


class _FakeLLM:
    """Scripted complete(): items are either exceptions or response objects."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        item = self._rounds.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(*, text: str = "", tool_calls: list | None = None,
          model: str = "m") -> SimpleNamespace:
    return SimpleNamespace(
        text=text, model=model,
        raw={"tool_calls": list(tool_calls or [])},
        input_tokens=0, output_tokens=0,
        provider="p", latency_ms=0, finish_reason="stop",
    )


def _call(name: str, args_json: str = "{}", cid: str = "c1") -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args_json}}


def _register_tool(name: str, handler, capability: str = "test:fix") -> None:
    from connectors.builtin import get_registry
    from core.agent.base_agent import Tool
    get_registry().register(Tool(name=name, capability=capability,
                                 description="engine-fix test tool",
                                 handler=handler))


# ---------------------------------------------------------------------------
# turn engine (fix 3 — now the Universe turn engine)
# ---------------------------------------------------------------------------

async def test_tool_call_has_hard_timeout(fresh_home, monkeypatch):
    monkeypatch.setenv("GAX_TOOL_TIMEOUT_SECONDS", "0.2")
    seen = {"n": 0}

    async def hang(**_kwargs):
        seen["n"] += 1
        await asyncio.sleep(5)  # would hang the turn forever
        return {"ok": True}

    _register_tool("HangForever", hang)
    # tool calls are whitelist-scoped to registered agents (fail-closed)
    from core.core_agents.agents import get_agent
    get_agent("universe").tool_whitelist_names.append("HangForever")
    from core.agent.turn_engine import run_turn
    resp, events, out = await run_turn(
        llm_client=_FakeLLM([_resp(tool_calls=[_call("HangForever")]),
                             _resp(text="gave up honestly")]),
        messages=[{"role": "user", "content": "hi"}],
        agent="universe", turn_id="g-timeout",
        response_format=None, max_tokens=256,
        allowed_tools=None)
    hang_events = [e for e in events if e["tool"] == "HangForever"]
    assert hang_events and seen["n"] == 1
    assert "timed out" in str(hang_events[0]["result"].get("error", ""))
    assert resp.text == "gave up honestly"  # the turn CONTINUED after the timeout
    assert out.stop_reason == "no_tool_calls"


async def test_duplicate_cap_blocks_and_polling_tools_exempt(fresh_home):
    counts = {"dup": 0, "poll": 0}

    async def dup_handler(**_k):
        counts["dup"] += 1
        return {"ok": True, "n": counts["dup"]}

    async def poll_handler(**_k):
        counts["poll"] += 1
        return {"ok": True, "n": counts["poll"]}

    _register_tool("CountOnce", dup_handler)
    _register_tool("Monitor", poll_handler)
    from core.agent.base_agent import POLLING_TOOLS
    assert POLLING_TOOLS == frozenset({"TaskOutput", "Monitor"})
    # tool calls are whitelist-scoped to registered agents (fail-closed)
    from core.core_agents.agents import get_agent
    gal_whitelist = get_agent("universe").tool_whitelist_names
    gal_whitelist.append("CountOnce")
    if "Monitor" not in gal_whitelist:
        gal_whitelist.append("Monitor")
    rounds = [_resp(tool_calls=[_call("CountOnce", '{"x": 1}')]),
              _resp(tool_calls=[_call("CountOnce", '{"x": 1}')]),
              _resp(tool_calls=[_call("CountOnce", '{"x": 1}')]),
              _resp(tool_calls=[_call("CountOnce", '{"x": 1}')]),
              _resp(tool_calls=[_call("CountOnce", '{"x": 1}')]),
              _resp(tool_calls=[_call("Monitor", '{"id": "t"}')]),
              _resp(tool_calls=[_call("Monitor", '{"id": "t"}')]),
              _resp(tool_calls=[_call("Monitor", '{"id": "t"}')]),
              _resp(tool_calls=[_call("Monitor", '{"id": "t"}')]),
              _resp(text="done")]
    from core.agent.turn_engine import run_turn
    _, events, out = await run_turn(
        llm_client=_FakeLLM(rounds),
        messages=[{"role": "user", "content": "hi"}],
        agent="universe", turn_id="g-dup",
        response_format=None, max_tokens=256,
        allowed_tools=None)
    dup_results = [e["result"] for e in events if e["tool"] == "CountOnce"]
    poll_results = [e["result"] for e in events if e["tool"] == "Monitor"]
    # identical CountOnce calls: the FIFTH identical signature is blocked
    # (the cap allows four identical calls before refusing)
    assert counts["dup"] == 4  # four executed
    assert any("duplicate tool call blocked" in str(r.get("error", ""))
               for r in dup_results)
    # polling re-polls the SAME id — never capped
    assert counts["poll"] == 4
    assert all(r.get("ok") for r in poll_results)
    assert out.stop_reason == "no_tool_calls"


async def test_malformed_reply_drops_to_safe_token_cap(fresh_home):
    from core.agent.base_agent import MALFORMED_RETRY_MAX_TOKENS
    from providers.client import LLMError
    assert MALFORMED_RETRY_MAX_TOKENS == 8192
    fake = _FakeLLM([
        LLMError("provider 400", kind="malformed", provider="p", model="m"),
        _resp(text=json.dumps({"what_was_done": "recovered"})),
    ])
    from core.agent.turn_engine import run_turn
    resp, _events, _out = await run_turn(
        llm_client=fake,
        messages=[{"role": "user", "content": "hi"}],
        agent="universe", turn_id="g-malformed",
        response_format={"type": "json_object"},
        max_tokens=16384,
        allowed_tools=None)
    assert fake.calls[0]["max_tokens"] == 16384
    assert fake.calls[1]["max_tokens"] == MALFORMED_RETRY_MAX_TOKENS
    assert "what_was_done" in resp.text


def test_effort_map_single_source():
    from core.agent.base_agent import EFFORT_DEFAULT_MAX_TOKENS, EFFORT_MAX_TOKENS
    assert EFFORT_MAX_TOKENS == {"low": 2048, "medium": 4096, "high": 16384}
    assert EFFORT_DEFAULT_MAX_TOKENS == 4096


def test_approx_tokens_counts_tool_args_and_none_content():
    from core.agent.base_agent import _approx_tokens
    messages = [
        {"role": "system", "content": "sys prompt"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"function": {"name": "Read", "arguments": '{"path": "a.py"}'}}]},
        {"role": "user", "content": "hello"},
    ]
    n = _approx_tokens(messages)
    assert n > 0  # None content no longer crashes the estimate


# ---------------------------------------------------------------------------
# gravity wiring (fix 2)
# ---------------------------------------------------------------------------

def test_tool_verified_turn_reaches_idle_promote_threshold():
    from config import SUBCONSCIOUS_PROMOTE_AT
    from core.memory.galactic_core import compute_gravity
    handoff = {"agent": "universe", "decision_confidence": 0.5,
               "is_knowledge_based": True, "task_success": True,
               "tools_used": ["LS", "Read"]}
    gravity, prov = compute_gravity([handoff])
    # (0.5 × 1.0 × 0.6) + (1.0 × 0.4) = 0.70 — the idle-promote bar must be
    # reachable by a normal successful tool turn, or nothing is ever promoted
    assert gravity >= SUBCONSCIOUS_PROMOTE_AT
    assert prov.knowledge_based_flags == [True]
    # WITHOUT the orchestrator fix (kb=False default) the same turn scored
    # 0.40 — below the bar; pin the contrast so a regression is visible
    blind, _ = compute_gravity([{**handoff, "is_knowledge_based": False}])
    assert blind < SUBCONSCIOUS_PROMOTE_AT


# ---------------------------------------------------------------------------
# swarm (fix 7)
# ---------------------------------------------------------------------------

def test_swarm_clear_drops_mailboxes_and_spawns(fresh_home):
    from core.agent import swarm
    swarm._goal_spawns["g-mail"] = {"total": 0}
    delivered = swarm.deliver_message(to="research", message="m",
                                      turn_id="g-mail", from_agent="x")
    assert delivered["ok"] is True
    swarm._goal_spawns["g-other"] = {"total": 0}
    swarm.deliver_message(to="research", message="m2",
                          turn_id="g-other", from_agent="y")
    swarm.clear("g-mail")
    assert ("g-mail", "research") not in swarm._mailboxes
    assert "g-mail" not in swarm._goal_spawns
    # other goals are untouched
    assert ("g-other", "research") in swarm._mailboxes
    assert swarm.drain_mailbox("g-other", "research")[0]["message"] == "m2"


async def test_spawn_reports_budget_skipped_missions_honestly(fresh_home, monkeypatch):
    monkeypatch.setenv("GAX_SWARM_MAX_TOTAL_PER_GOAL", "1")
    from core.agent import swarm
    res = await swarm.spawn(agent="universe", turn_id="g-budget", missions=[
        {"role": "worker", "brief": "research topic one"},
        {"role": "worker", "brief": "research topic two"},
    ])
    assert res["ok"] is True
    assert res["skipped_budget"] == 1
    skipped = [r for r in res["results"] if r.get("skipped") == "budget"]
    assert len(skipped) == 1
    assert skipped[0]["success"] is False
    assert skipped[0]["what_was_done"].startswith("skipped: budget")


# ---------------------------------------------------------------------------
# hooks (fix 10)
# ---------------------------------------------------------------------------

def test_pre_shell_event_covers_all_shell_tools(fresh_home):
    from hooks.manager import HookManager
    hm = HookManager()
    for tool in ("Bash", "PowerShell", "CodeExecute"):
        assert hm._event_applies("pre_shell", tool) is True
    assert hm._event_applies("pre_shell", "BrowserNavigate") is False
    assert hm._event_applies("pre_write", "Write") is True
    assert hm._event_applies("pre_write", "Bash") is False


def test_hook_blocks_powershell(fresh_home):
    from hooks.manager import HookManager
    hm = HookManager()
    hm._hooks.append({"hook_id": "test-block", "event": "pre_shell",
                      "matcher": "*", "action": "block",
                      "message": "no shell for you", "enabled": True,
                      "builtin": False})
    block = hm.check("pre_shell", "PowerShell", agent="test",
                     turn_id="g-hook", args={})
    assert block is not None and block.action == "block"
    # and the builtin Bash guard keeps firing for Bash
    bash_block = hm.check("pre_shell", "Bash", agent="test",
                          turn_id="g-hook", args={})
    assert bash_block is not None


def test_require_facts_one_shot_and_clear_goal_state(fresh_home):
    from hooks.manager import HookManager
    hm = HookManager()
    hm._hooks.append({"hook_id": "test-facts", "event": "pre_shell",
                      "matcher": "*", "action": "require_facts",
                      "message": "state your facts", "enabled": True,
                      "builtin": False})
    b1 = hm.check("pre_shell", "CodeExecute", agent="a", turn_id="g1", args={})
    assert b1 is not None
    b2 = hm.check("pre_shell", "CodeExecute", agent="a", turn_id="g1", args={})
    assert b2 is None  # one-shot: the retry passes
    hm.clear_goal_state("g1")
    b3 = hm.check("pre_shell", "CodeExecute", agent="a", turn_id="g1", args={})
    assert b3 is not None  # bookkeeping dropped with the goal
    # another goal is unaffected by the clear
    assert ("g-other", "test-facts") not in hm._facts_shown
