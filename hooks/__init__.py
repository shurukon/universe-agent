"""hooks/ — the Universe hook layer (the GateGuard twin).

A hook is a declarative rule checked at the ToolRegistry chokepoint:
  * pre_tool   — every tool call, before the capability gate
  * pre_shell  — shell-adjacent tools specifically (Bash, PowerShell,
    CodeExecute)
  * pre_write  — Write / Edit calls specifically
  * post_tool  — every successful call, observe-only (v1)

Actions:
  * require_facts — the FIRST matching call per goal is blocked with the
    hook's message (the model must present its plan/facts, then retry);
    later calls pass. One-shot because content verification without a
    judge model is theater (this is exactly how ECC's GateGuard behaves
    in practice: block once, the retry with facts goes through).
  * block        — every matching call is blocked (a hard rule).

Every interception emits hook_intercepted so the UI's Hooks badge (Universe
parity, Phase 2) can show what stopped what.
"""
from hooks.manager import (
    HookBlock,
    HookManager,
    get_hook_manager,
    reset_hook_manager_for_tests,
)

__all__ = ["HookBlock", "HookManager", "get_hook_manager",
           "reset_hook_manager_for_tests"]
