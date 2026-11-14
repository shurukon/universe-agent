"""tests/test_turn_terminal_events.py — P6: the bridge must close every
turn with a bus event.

The bridge used to emit nothing after `run_turn` returned, so the client's
running strip un-mounted only by timing out — a turn that failed looked
identical to one that was still working. These tests pin the terminal
events (turn_completed / turn_failed / provider_halt / turn_cancelled)
against the real bus.
"""
from __future__ import annotations



class _ScriptedLLM:
    """Answers once with text, or raises on the first call."""

    def __init__(self, text: str = "done", error=None):
        self._text = text
        self._error = error
        self.calls = 0

    async def complete(self, **kw):
        self.calls += 1
        from types import SimpleNamespace
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            text=self._text, model="m", usage=None,
            raw={"tool_calls": []}, input_tokens=1, output_tokens=1,
            provider="p", latency_ms=1, finish_reason="stop")


def _drive_bridge(monkeypatch, llm, text: str = "hello"):
    """POST /api/turn against the real bridge with a scripted client."""
    from fastapi.testclient import TestClient
    from providers import manager as pm_mod
    import universe_desktop_bridge.app as app_mod

    pm = pm_mod.get_provider_manager()

    class _FakePM:
        def best_available(self, agent):
            return ("fake", "fake-model")

        def get_agent_model(self, agent):
            return ("fake", "fake-model")

        def get_fallback_chain(self, agent):
            return []

    pm.best_available = _FakePM().best_available
    pm.get_agent_model = _FakePM().get_agent_model
    pm.get_fallback_chain = _FakePM().get_fallback_chain

    from core.agent import universe_agent as ua
    monkeypatch.setattr(ua.get_universe_agent(), "llm", llm, raising=False)
    app_mod.reset_runtime_for_tests()
    from observability.events import reset_event_bus_for_tests
    reset_event_bus_for_tests()
    client = TestClient(app_mod.app)
    try:
        r = client.post("/api/turn", json={"text": text})
        assert r.status_code == 200, r.text
        return client, r.json()
    except Exception:
        client.close()
        raise


def _events(client, turn_id: str, wait_for: str | None = None,
            timeout_s: float = 15.0):
    """Read the turn's event types, optionally waiting for a terminal type
    (the bridge runs the turn on its own loop, in parallel)."""
    import time
    deadline = time.monotonic() + timeout_s
    types: list[str] = []
    while True:
        r = client.get(f"/api/turn/{turn_id}/events")
        assert r.status_code == 200, r.text
        types = [e.get("type") for e in r.json()["events"]]
        if not wait_for or wait_for in types:
            return types
        if time.monotonic() >= deadline:
            return types
        time.sleep(0.1)


def test_completed_turn_emits_turn_completed(fresh_home, monkeypatch):
    client, body = _drive_bridge(monkeypatch, _ScriptedLLM("all done"))
    try:
        types = _events(client, body["turn_id"], wait_for="turn_completed")
        assert "turn_started" in types, types
        assert "turn_completed" in types, types
    finally:
        client.close()


def test_failed_turn_emits_turn_failed(fresh_home, monkeypatch):
    from providers.client import LLMError
    client, body = _drive_bridge(
        monkeypatch,
        _ScriptedLLM(error=LLMError("dead provider", kind="provider_down")))
    try:
        terminal = _events(client, body["turn_id"],
                           wait_for="provider_halt")
        assert "turn_started" in terminal, terminal
        assert "provider_halt" in terminal or "turn_failed" in terminal, (
            terminal)
    finally:
        client.close()
