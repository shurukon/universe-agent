"""tests/test_context_window.py — A6: the compaction budget was a hardwired
32 000 no provider had ever reported.

`run_turn(context_window_tokens=32_000)` was the default everywhere, the
session engine carried a copy of the same constant, and
`context/compaction.context_window_for_model` returned the (window, flag)
TUPLE from security.capability straight into compaction maths. The catalog
now declares each provider's window and the lookup honours it.
"""
from __future__ import annotations


def test_catalog_declares_windows_for_the_live_providers():
    from providers.catalog import PROVIDER_CATALOG
    declared = [p for p in PROVIDER_CATALOG if getattr(p, "context_window", 0)]
    assert len(declared) >= 25, (
        f"only {len(declared)} presets declare a context window — the meter "
        "and compaction both need it")
    for p in declared:
        assert p.context_window > 0, p.name
    # the recommended free provider is covered
    by_name = {p.name: p for p in PROVIDER_CATALOG}
    assert by_name["Google AI Studio"].context_window == 1_000_000
    assert by_name["Anthropic"].context_window == 200_000


def test_lookup_prefers_the_catalog_over_the_heuristic():
    from security.capability import _context_window_for
    window, is_estimate = _context_window_for("claude-sonnet-4-5")
    assert window == 200_000
    assert is_estimate is False, "a catalog-declared window is NOT an estimate"
    # a name the catalog does not know falls back to the family heuristic
    # and is honestly flagged as an estimate
    window, is_estimate = _context_window_for("acme-unknown-9000")
    assert is_estimate is True
    assert window > 0


def test_compaction_helper_returns_an_int():
    """The tuple leaked into `window * COMPACTION_TRIGGER_PCT` — an int is
    the contract."""
    from context.compaction import context_window_for_model
    window = context_window_for_model("claude-sonnet-4-5")
    assert isinstance(window, int), type(window)
    assert window == 200_000


def test_session_loop_passes_the_active_model_window():
    """run_session_turn must hand run_turn the resolved window, not the
    hardwired default."""
    from pathlib import Path
    src = Path("core/engine/session_loop.py").read_text(encoding="utf-8")
    assert "_context_window(llm_client)" in src, (
        "the session loop must resolve the active model's window")
    assert "context_window_tokens=model_window" in src, (
        "the resolved window must reach run_turn")
    assert "context_window_tokens=32_000" not in src, (
        "the hardwired 32 000 must not be passed to run_turn")


def test_session_loop_compaction_uses_the_resolved_window(monkeypatch):
    """The window the session loop hands the engine is the ACTIVE model's
    window — never a hardwired constant.

    The test pins it deterministically: the provider manager is told which
    model is active, and the resolved window must equal that model's
    catalog window. (Without the pin the assertion depended on whichever
    provider an EARLIER test had configured — 32 000 here, 64 000 there.)"""
    from core.engine.session_loop import _context_window
    from context.compaction import context_window_for_model
    from providers import manager as pm_mod

    pm = pm_mod.get_provider_manager()

    class _FakePM:
        def best_available(self, agent):
            return ("fake", "claude-sonnet-4-5")

    monkeypatch.setattr(pm, "best_available", _FakePM().best_available)
    window = _context_window(_StubClient())
    assert window == context_window_for_model("claude-sonnet-4-5")
    assert window == 200_000
    assert window != 32_000, "the hardwired 32 000 must never be used"


def test_session_loop_falls_back_when_no_model_resolves(monkeypatch):
    """No active model (nothing configured) → the documented fallback."""
    from core.engine.session_loop import _context_window, SESSION_CONTEXT_WINDOW_TOKENS
    from providers import manager as pm_mod

    pm = pm_mod.get_provider_manager()
    monkeypatch.setattr(pm, "best_available", lambda agent: None)
    window = _context_window(_StubClient())
    assert window == SESSION_CONTEXT_WINDOW_TOKENS


class _StubClient:
    pass
