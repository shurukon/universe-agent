"""hooks/builtin_factguard.py — the builtin fact-forcing hooks.

The ECC GateGuard philosophy embedded in Universe: before the FIRST shell
command and the FIRST file write of each goal, the agent must have shown
its work — what it is about to do and why. Enforcement is one block per
goal per hook with an instructive message; the retried call goes through
(the model is expected to state its facts in between). Builtins can be
disabled from settings but never removed from the list — the parity
table (docs/parity.md) pins them.
"""
from __future__ import annotations

SHELL_FACTS_MESSAGE = (
    "FACT-FORCING GATE — before the first shell command of this goal, "
    "present the facts in your reply (then re-issue the command): "
    "(1) the goal in one sentence, (2) what this command does, "
    "(3) what result you expect. One block per goal."
)

WRITE_FACTS_MESSAGE = (
    "FACT-FORCING GATE — before the first file write of this goal, "
    "present the facts in your reply (then re-issue the write): "
    "(1) the goal in one sentence, (2) the file you are writing and why, "
    "(3) what the file will contain. One block per goal."
)

BUILTIN_HOOKS: list[dict] = [
    {
        "hook_id": "builtin-factguard-shell",
        "event": "pre_shell",
        "matcher": "Bash",
        "action": "require_facts",
        "message": SHELL_FACTS_MESSAGE,
        "enabled": True,
        "builtin": True,
    },
    {
        "hook_id": "builtin-factguard-write",
        "event": "pre_write",
        "matcher": "*",
        "action": "require_facts",
        "message": WRITE_FACTS_MESSAGE,
        "enabled": True,
        "builtin": True,
    },
]
