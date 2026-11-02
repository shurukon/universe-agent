"""hooks/manager.py — hook evaluation at the tool chokepoint.

Loaded once per process: the builtin hooks (factguard — the GateGuard
twin) merged with the user's desktop.hooks[] config (config.json, the
same store /api/settings writes). Evaluation is PURE: check() never
mutates anything except its own one-shot bookkeeping, so a hook can
never corrupt a call — only stop it with an honest message.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

HOOK_EVENTS = ("pre_tool", "pre_shell", "pre_write", "post_tool")
HOOK_ACTIONS = ("require_facts", "block")

# which concrete tools an event covers (the event narrows the domain; the
# matcher only picks tools within it). THE one shared domain map (audit H3):
# connectors/builtin used to keep its own wider copy (BashUnsandboxed,
# Monitor, ApplyPatch, NotebookEdit) while this map stayed narrow — tools
# present only in the wider copy skipped hook evaluation entirely. Import
# this map from hooks.manager everywhere; never duplicate it.
_EVENT_TOOLS: dict[str, tuple[str, ...]] = {
    "pre_shell": ("Bash", "BashUnsandboxed", "PowerShell", "CodeExecute",
                  "Monitor"),
    "pre_write": ("Write", "Edit", "ApplyPatch", "NotebookEdit"),
}


@dataclass(frozen=True)
class HookBlock:
    """A hook that stopped a call. The registry turns this into the
    tool_blocked result + hook_intercepted event."""
    hook_id: str
    event: str
    action: str
    message: str


class HookManager:
    """Declarative hooks: builtins + user config, checked pre/post gate."""

    def __init__(self) -> None:
        self._hooks: list[dict[str, Any]] = []
        # (turn_id, hook_id) one-shot state for require_facts
        self._facts_shown: set[tuple[str, str]] = set()
        self._load()

    # -- config ---------------------------------------------------------------

    def _load(self) -> None:
        """Builtins first, then the user's desktop.hooks[] (config.json).

        Phase 3 kill switch: desktop.hooks_enabled (default True) gates the
        WHOLE layer. When it is False nothing is loaded — builtins included —
        so check() passes every call until Settings turns it back on. The
        bridge reloads this manager on every settings PUT, so the toggle
        takes effect on the very next call without a restart.
        """
        self._hooks = []
        try:
            from config import get_config
            desktop = get_config().get("desktop", {})
        except Exception:
            desktop = {}
        if not isinstance(desktop, dict):
            desktop = {}
        if not desktop.get("hooks_enabled", True):
            return  # disabled from Settings: no hooks to evaluate
        from hooks.builtin_factguard import BUILTIN_HOOKS
        for h in BUILTIN_HOOKS:
            self._hooks.append(dict(h))
        user_hooks = desktop.get("hooks", [])
        if isinstance(user_hooks, list):
            for i, h in enumerate(user_hooks):
                if not isinstance(h, dict):
                    continue
                hook = self._normalize_user_hook(h, i)
                if hook is not None:
                    self._hooks.append(hook)

    def reload(self) -> None:
        """Re-read config (called by the bridge after a settings PUT)."""
        self._load()

    @staticmethod
    def _normalize_user_hook(h: dict[str, Any], i: int) -> dict[str, Any] | None:
        event = str(h.get("event", "")).strip()
        action = str(h.get("action", "")).strip()
        if event not in HOOK_EVENTS or action not in HOOK_ACTIONS:
            return None  # a malformed entry is skipped, never crashes calls
        return {
            "hook_id": f"user-{i}-{event}-{action}",
            "event": event,
            "matcher": str(h.get("matcher", "*")).strip() or "*",
            "action": action,
            "message": str(h.get("message", ""))[:400]
                       or f"hook {event} blocked this call",
            "enabled": bool(h.get("enabled", True)),
            "builtin": False,
        }

    # -- evaluation ------------------------------------------------------------

    def _event_applies(self, event: str, tool_name: str) -> bool:
        """pre_tool matches everything; the specific events match their
        concrete tools only."""
        if event == "pre_tool":
            return True
        return tool_name in _EVENT_TOOLS.get(event, ())

    def check(self, event: str, tool_name: str, *, agent: str,
              turn_id: str, args: dict[str, Any]) -> HookBlock | None:
        """Pre-call evaluation. Returns the block that stops the call, or
        None when every matching hook passes. post_tool hooks never block
        (observe-only v1)."""
        if event not in HOOK_EVENTS or event == "post_tool":
            return None
        for h in self._hooks:
            if not h.get("enabled", True):
                continue
            # a hook fires ONLY on its own declared event — a pre_write hook
            # with matcher "*" must never block a pre_tool check (it did, and
            # silently broke every non-file tool call until caught by the
            # harness tests)
            if h.get("event") != event:
                continue
            if not self._event_applies(event, tool_name):
                continue
            # the specific events (pre_shell/pre_write) already scope the
            # domain; the matcher then picks tools within it. For pre_tool
            # the matcher alone decides.
            if not fnmatch.fnmatch(tool_name, h["matcher"]):
                continue
            if h["action"] == "block":
                return HookBlock(h["hook_id"], event, "block", h["message"])
            # require_facts: one block per goal — the retry passes
            key = (turn_id, h["hook_id"])
            if key in self._facts_shown:
                continue
            self._facts_shown.add(key)
            return HookBlock(h["hook_id"], event, "require_facts", h["message"])
        return None

    def observe(self, event: str, tool_name: str, *, agent: str,
                turn_id: str, args: dict[str, Any]) -> None:
        """post_tool: nothing more than the tap exists for in v1 — kept so
        validation hooks (a test-runner hook, a lint hook) can land later
        without touching the registry again."""
        # deliberately empty: v1 post hooks are announce-only via config
        return None

    def clear_goal_state(self, turn_id: str) -> None:
        """Goal ended: drop its one-shot require_facts bookkeeping. The
        (turn_id, hook_id) entries used to accumulate forever (engine-fix
        round); the orchestrator calls this from its goal-cleanup finally."""
        self._facts_shown = {
            key for key in self._facts_shown if key[0] != turn_id}


_manager: HookManager | None = None


def get_hook_manager() -> HookManager:
    global _manager
    if _manager is None:
        _manager = HookManager()
    return _manager


def reset_hook_manager_for_tests() -> HookManager:
    global _manager
    _manager = HookManager()
    return _manager
