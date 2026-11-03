"""security/child_env.py — sanitized environment for tool-spawned subprocesses.

Every process Universe spawns on behalf of the agent (shell commands, sandboxed
children, MCP servers, code execution) inherits the parent environment today.
That leaks UNIVERSE_BRIDGE_TOKEN (per-launch bearer token for the desktop
bridge: 127.0.0.1:8738 — one approved shell call can read it and then approve
every later "explicit" consent via the bridge API) and UNIVERSE_PASSPHRASE
(decrypts every stored provider key), plus any provider API keys that happen
to live in env vars.

Fail-closed design: the denylist matches Universe's own secrets *and* generic
key-like names, so a future UNIVERSE_* secret added without updating this module
is still stripped by the prefix rule.
"""
from __future__ import annotations

import os
import re

# Exact names always stripped from child environments.
_DENY_EXACT = {
    "UNIVERSE_BRIDGE_TOKEN",
    "UNIVERSE_PASSPHRASE",
    "UNIVERSE_TEST_API_KEY",
}

# Any env var matching one of these patterns is stripped. Keep deliberately
# broad: a false positive (removing a var a child legitimately needs) is
# recoverable by passing an explicit env override; a false negative leaks a
# secret to agent-controlled code.
_DENY_PATTERNS = re.compile(
    r"^(UNIVERSE_[A-Z0-9_]*_KEY|UNIVERSE_[A-Z0-9_]*_TOKEN|UNIVERSE_[A-Z0-9_]*_SECRET"
    r"|.*_API_KEY|.*_API_TOKEN|.*_ACCESS_TOKEN|.*_SECRET_KEY)$"
)


def sanitized_child_env() -> dict[str, str]:
    """A copy of os.environ safe to hand to agent-driven subprocesses.

    Callers may layer their own additions on top (the child still needs
    PATH/TEMP/SystemRoot etc. to run at all):
        env={**sanitized_child_env(), "MY_TOOL_VAR": "1"}
    """
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        if name in _DENY_EXACT:
            continue
        if _DENY_PATTERNS.match(name):
            continue
        env[name] = value
    return env


def sanitize_env(env: dict[str, str] | None) -> dict[str, str]:
    """Sanitize an explicit env dict, then layer it on the sanitized parent.

    Used at spawn sites that accept caller-supplied env additions (shell._run,
    MCP servers): additions survive unless they themselves
    carry a denied name, in which case they are dropped — the deny rule always
    wins over caller overrides.
    """
    merged = sanitized_child_env()
    if env:
        for name, value in env.items():
            if name in _DENY_EXACT or _DENY_PATTERNS.match(name):
                continue
            merged[name] = value
    return merged
