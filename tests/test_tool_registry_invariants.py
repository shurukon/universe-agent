"""tests/test_tool_registry_invariants.py — no registered tool may be
unreachable.

Audit 2026-11-10: the registry held 135 tools while the universe agent's
whitelist named 121. The 14 orphans (BashUnsandboxed, CodeAnalyzer,
ColorContrastCheck, CoverageReport, CssTools, DependencyAudit,
SecretScanner, StaticAnalysis, SvgGen, TestRunner, VulnScanner, js,
js_add_node_module_dir, js_reset) could never be called — the gate denied
them with blocked_by="whitelist" — yet ToolSearch advertised them as
discoverable. They were DELETED. This file pins the invariant that makes
that class of dead weight impossible to reintroduce silently.
"""
from __future__ import annotations


def test_registry_equals_whitelist(fresh_home):
    from connectors.builtin import get_registry
    from core.core_agents import get_agent
    registered = set(get_registry().names())
    whitelisted = set(get_agent("universe").tool_whitelist_names)
    assert registered == whitelisted, (
        "registered-but-unreachable tools: "
        f"{sorted(registered - whitelisted)}; whitelisted-but-missing: "
        f"{sorted(whitelisted - registered)}")
    assert len(registered) == 125, f"tool count changed: {len(registered)}"


def test_no_duplicate_registrations(fresh_home):
    from connectors.builtin import get_registry
    reg = get_registry()
    names = reg.names()
    assert len(names) == len(set(names))


def test_toolsearch_cannot_advertise_unreachable_tools(fresh_home):
    """ToolSearch searches the registry — with registry == whitelist it can
    no longer suggest a tool the gate would refuse."""
    from connectors.builtin import get_registry
    from core.core_agents import get_agent
    registered = set(get_registry().names())
    whitelisted = set(get_agent("universe").tool_whitelist_names)
    assert registered <= whitelisted
