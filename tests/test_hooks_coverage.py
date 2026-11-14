"""tests/test_hooks_coverage.py — the hook routing must not drift again.

Audit 2026-11-10: the registry (connectors/builtin/__init__.py) routed
Monitor / ApplyPatch / NotebookEdit to pre_shell / pre_write, while
hooks/manager.py filtered them out — so the builtin fact-forcing gate and
every user hook were silently bypassed by exactly those tools. The sets now
live in ONE place (hooks/manager.py) and this file pins the agreement.
"""
from __future__ import annotations

import pytest

from hooks.manager import PRE_SHELL_TOOLS, PRE_WRITE_TOOLS, _EVENT_TOOLS


def test_event_tool_sets_cover_every_routed_tool():
    assert set(_EVENT_TOOLS["pre_shell"]) == {"Bash", "PowerShell",
                                              "CodeExecute", "Monitor"}
    assert set(_EVENT_TOOLS["pre_write"]) == {"Write", "Edit",
                                              "ApplyPatch", "NotebookEdit"}
    assert tuple(_EVENT_TOOLS["pre_shell"]) == PRE_SHELL_TOOLS
    assert tuple(_EVENT_TOOLS["pre_write"]) == PRE_WRITE_TOOLS


def test_registry_uses_the_manager_sets():
    """The registry imports the manager's tuples — no second copy."""
    import connectors.builtin as reg_mod
    src = reg_mod.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "from hooks.manager import PRE_SHELL_TOOLS, PRE_WRITE_TOOLS" in text
    # and it must not define its own sets any more
    assert '_SHELL_TOOLS = {"Bash"' not in text
    assert '_WRITE_TOOLS = {"Write"' not in text


@pytest.mark.asyncio
async def test_monitor_fires_the_pre_shell_hook(fresh_home, monkeypatch):
    """A Monitor call must reach the pre_shell hook layer."""
    from connectors.builtin import get_registry
    from hooks import get_hook_manager, reset_hook_manager_for_tests
    reset_hook_manager_for_tests()
    seen: list[tuple[str, str]] = []
    real_check = get_hook_manager().check

    def spy(event, tool_name, **kw):
        seen.append((event, tool_name))
        return real_check(event, tool_name, **kw)

    monkeypatch.setattr(get_hook_manager(), "check", spy)
    reg = get_registry()
    await reg.call("Monitor", agent="universe", turn_id="g1",
                   args={"command": "echo hi"})
    assert ("pre_shell", "Monitor") in seen


def test_factguard_shell_hook_covers_every_shell_tool():
    """The builtin shell fact-gate matcher must not be narrowed to Bash."""
    from hooks.builtin_factguard import BUILTIN_HOOKS
    shell = [h for h in BUILTIN_HOOKS if h["event"] == "pre_shell"]
    assert shell, "the builtin shell fact-guard must exist"
    assert shell[0]["matcher"] == "*"
