"""tests/test_turn_scheduler.py — A4: the concurrency scheduler must derive
readOnly/concurrentSafe from the REAL capability namespace.

The old `READ_CAPABILITIES` set mixed tool NAMES into a set of capability
strings ("VisionAnalyze" sat beside "Read"/"Search"), so no real read tool
matched it: `file.read` and `memory.read` were both classified mutating and
NOTHING ran in parallel. These tests pin the corrected derivation, the
ordering guarantee, and the parallel cap.
"""
from __future__ import annotations

import time

from tests._engine_harness import (FakeLLM, ProbeRegistry, call, resp,
                                   run_turn_fresh)


class _Tracker:
    """Records concurrent execution depth per tool."""

    def __init__(self):
        self.lock = __import__("threading").Lock()
        self.active = 0
        self.max_active = 0
        self.order: list[str] = []

    def wrap(self, name: str, delay: float = 0.05):
        def handler(**_):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.order.append(name)
            time.sleep(delay)
            with self.lock:
                self.active -= 1
            return {"ok": True, "name": name}
        return handler


def test_read_capabilities_are_the_real_namespace():
    from core.agent.turn_engine import READ_CAPABILITIES
    # these are capability STRINGS the registry actually declares
    for cap in ("Read", "file.read", "git.read", "memory.read",
                "automation.read", "ToolSearch"):
        assert cap in READ_CAPABILITIES, cap
    # tool NAMES must stay out of it (the old bug)
    assert "VisionAnalyze" not in READ_CAPABILITIES
    assert "WebSearch" not in READ_CAPABILITIES


def test_classification_uses_the_real_capability():
    from core.agent.turn_engine import _classify_tool_call
    from types import SimpleNamespace

    read_tool = SimpleNamespace(consent="auto", capability="file.read")
    meta = _classify_tool_call("Read", read_tool)
    assert meta["readOnly"] is True
    assert meta["concurrentSafe"] is True
    assert meta["sideEffectScope"] == "none"

    net = SimpleNamespace(consent="auto", capability="network.req")
    meta = _classify_tool_call("HttpClient", net)
    assert meta["readOnly"] is False
    assert meta["concurrentSafe"] is False
    assert meta["sideEffectScope"] == "network"

    write = SimpleNamespace(consent="auto", capability="Write")
    meta = _classify_tool_call("Write", write)
    assert meta["concurrentSafe"] is False
    assert meta["sideEffectScope"] == "files"

    danger = SimpleNamespace(consent="explicit", capability="file.read")
    meta = _classify_tool_call("Delete", danger)
    # explicit consent never runs in parallel, even for a read capability
    assert meta["concurrentSafe"] is False
    assert meta["destructive"] is True


def test_consecutive_reads_run_in_parallel():
    """Four read-only probes issued in one step must actually overlap
    (they are grouped), and their results commit to the context in
    DECLARATION order — never the order they happened to finish in."""
    tracker = _Tracker()
    entries = [(f"__Read{i}__", tracker.wrap(f"__Read{i}__"),
                "file.read", "auto") for i in range(4)]
    with ProbeRegistry(entries):
        llm = FakeLLM([
            resp(tool_calls=[call(f"__Read{i}__", "{}", f"c{i}")
                             for i in range(4)]),
            resp(text="read them all"),
        ])
        _r, events, out = run_turn_fresh(llm_client=llm)
        assert out.stop_reason == "no_tool_calls"
        assert tracker.max_active > 1, (
            "concurrent-safe reads did not overlap: max_active="
            f"{tracker.max_active}")
        # every call produced a committed event
        assert {e.get("call_id") for e in events if e.get("tool")} == {
            "c0", "c1", "c2", "c3"}
        # the CONTEXT rows the model sees are in declaration order
        second = llm.calls[1]["messages"]
        tool_ids = [m.get("tool_call_id") for m in second
                    if m.get("role") == "tool"]
        assert tool_ids == ["c0", "c1", "c2", "c3"], tool_ids


def test_parallel_batch_is_capped():
    """Six reads must not all run at once: the cap is a worker-pool bound,
    so exactly MAX_PARALLEL_TOOLS slots overlap."""
    from core.agent.turn_engine import MAX_PARALLEL_TOOLS
    tracker = _Tracker()
    n = MAX_PARALLEL_TOOLS + 2
    entries = [(f"__Cap{i}__", tracker.wrap(f"__Cap{i}__", 0.08),
                "file.read", "auto") for i in range(n)]
    with ProbeRegistry(entries):
        llm = FakeLLM([
            resp(tool_calls=[call(f"__Cap{i}__", "{}", f"c{i}")
                             for i in range(n)]),
            resp(text="done"),
        ])
        run_turn_fresh(llm_client=llm)
        assert tracker.max_active <= MAX_PARALLEL_TOOLS
        assert tracker.max_active > 1


def test_mutating_tool_runs_alone():
    """A write sandwiched between reads breaks the group: the write never
    runs alongside anything, and order is preserved."""
    tracker = _Tracker()
    entries = [
        ("__R1__", tracker.wrap("__R1__", 0.05), "file.read", "auto"),
        ("__W1__", tracker.wrap("__W1__", 0.02), "Write", "auto"),
        ("__R2__", tracker.wrap("__R2__", 0.05), "file.read", "auto"),
    ]
    with ProbeRegistry(entries):
        llm = FakeLLM([
            resp(tool_calls=[call("__R1__", "{}", "c1"),
                             call("__W1__", "{}", "c2"),
                             call("__R2__", "{}", "c3")]),
            resp(text="done"),
        ])
        _r, events, out = run_turn_fresh(llm_client=llm)
        assert out.stop_reason == "no_tool_calls"
        assert [e.get("call_id") for e in events if e.get("tool")] == [
            "c1", "c2", "c3"]
        # the write ran alone: it cannot have overlapped either read
        assert tracker.max_active == 1
