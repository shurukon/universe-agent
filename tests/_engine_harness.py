"""tests/_engine_harness.py — shared harness for the turn-engine tests.

A scripted LLM (`FakeLLM`) drives core.agent.turn_engine.run_turn exactly
like a provider would, and `register_probe` plants real registry tools. The
synthetic tool names all start with `__` so they can never collide with a
shipped tool, and each test removes them again in a finally-block (the
registry is process-global).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


class FakeLLM:
    """Scripted complete(): items are either exceptions or response objects."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if not self._rounds:
            raise AssertionError("FakeLLM ran out of scripted rounds")
        item = self._rounds.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def resp(*, text: str = "", tool_calls: list | None = None,
         model: str = "m", usage: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        text=text, model=model, usage=usage,
        raw={"tool_calls": list(tool_calls or [])},
        input_tokens=0, output_tokens=0,
        provider="p", latency_ms=0, finish_reason="stop",
    )


def call(name: str, args_json: str = "{}", cid: str = "c1") -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args_json}}


class ProbeRegistry:
    """Context manager: registers probe tools AND whitelists them for the
    universe agent (the gate is fail-closed: a registered tool outside the
    agent's whitelist is blocked with blocked_by="whitelist")."""

    def __init__(self, entries: list[tuple[str, object, str, str]]):
        # (name, handler, capability, consent)
        self.entries = entries
        self.registered: list[str] = []
        self.whitelisted: list[str] = []

    def __enter__(self):
        from connectors.builtin import get_registry
        from core.agent.base_agent import Tool
        from core.core_agents import get_agent
        reg = get_registry()
        wl = get_agent("universe").tool_whitelist_names
        for name, handler, capability, consent in self.entries:
            reg.register(Tool(name=name, capability=capability,
                              description="probe tool",
                              handler=handler, consent=consent))
            self.registered.append(name)
            if name not in wl:
                wl.append(name)
                self.whitelisted.append(name)
        return self

    def __exit__(self, *exc):
        from connectors.builtin import get_registry
        from core.core_agents import get_agent
        reg = get_registry()
        for name in self.registered:
            reg._tools.pop(name, None)
        wl = get_agent("universe").tool_whitelist_names
        for name in self.whitelisted:
            if name in wl:
                wl.remove(name)
        return False


def run_turn_fresh(**kwargs):
    """run_turn with the defaults every engine test needs.

    The gate is reset with auto-grant ON: these tests exercise the ENGINE,
    not the consent ladder (covered by tests/test_security_hardening*).
    """
    from core.agent.turn_engine import run_turn
    from hooks import reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    if kwargs.get("capability_gate") is None:
        # only reset the global gate when the caller did not supply one —
        # resetting would orphan the caller's gate while tools still call
        # get_gate() internally
        from security.capability import reset_gate_for_tests
        kwargs["capability_gate"] = reset_gate_for_tests()
    kwargs["capability_gate"].set_auto_grant(True)
    kwargs.setdefault("agent", "universe")
    kwargs.setdefault("turn_id", "t1")
    kwargs.setdefault("messages", [{"role": "user", "content": "go"}])
    return asyncio.run(run_turn(**kwargs))
