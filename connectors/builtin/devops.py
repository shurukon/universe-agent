"""connectors/builtin/devops.py — k8s + log_tail tools (Phase 3).

Two DevOps tools that plain `docker` doesn't cover:
  * k8s      — kubectl-equivalent get/describe/logs/apply with a
               plan-before-apply safety pattern: apply actions require an
               explicit `confirm=true` arg, returning the planned command first
               so the orchestrator can surface it for user consent.
  * log_tail — read the last N lines of a service/system log file. Pure stdlib,
               no external deps; reads files within the project scope.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry
from connectors.builtin.shell import _run_argv


# ---- k8s -----------------------------------------------------------------

# Safe read-only kubectl subcommands. Anything else (apply, delete, scale,
# edit, rollout) is a write and requires confirm=True.
_K8S_READ_OPS = {"get", "describe", "logs", "top", "explain", "version",
                 "cluster-info", "api-resources", "api-versions"}
_K8S_WRITE_OPS = {"apply", "delete", "create", "scale", "rollout", "edit",
                  "patch", "replace", "set", "annotate", "label"}


def k8s(action: str = "get", resource: str = "pods",
        name: str = "", namespace: str = "",
        manifest_path: str = "", file: str = "",
        confirm: bool = False,
        extra_args: str = "",
        timeout: int = 60) -> dict:
    """Plan-before-apply kubectl wrapper.
    Read actions (get/describe/logs/top/...) run immediately.
    Write actions (apply/delete/scale/...) return the planned command first; if
    `confirm=True` they execute, otherwise they return ok=False with the plan
    so the orchestrator can surface it to the user."""
    action = (action or "").strip().lower()
    if action not in _K8S_READ_OPS and action not in _K8S_WRITE_OPS:
        return {"ok": False, "error": f"unknown k8s action {action!r}",
                "allowed": sorted(_K8S_READ_OPS | _K8S_WRITE_OPS)}
    # build the kubectl command
    parts = ["kubectl"]
    if namespace:
        parts += ["-n", namespace]
    parts.append(action)
    if action in ("apply", "delete", "create") and (manifest_path or file):
        f = manifest_path or file
        parts += ["-f", f]
    elif action == "logs" and name:
        parts.append(name)
    elif action in ("get", "describe") and resource:
        parts.append(resource)
        if name:
            parts.append(name)
    else:
        # generic: pass resource and name
        if resource:
            parts.append(resource)
        if name:
            parts.append(name)
    if extra_args:
        parts += shlex.split(extra_args)
    # display string for the plan/consent surface only — execution below is
    # argv-list + shell=False, so shlex.quote's POSIX quoting being useless on
    # cmd.exe (the audit finding) no longer matters: nothing is ever parsed by
    # a host shell.
    cmd = " ".join(shlex.quote(p) for p in parts)
    is_write = action in _K8S_WRITE_OPS
    if is_write and not confirm:
        return {"ok": False,
                "error": "write action requires confirm=true",
                "blocked_by": "plan_before_apply",
                "planned_command": cmd,
                "note": "Surface this command to the user; re-call with confirm=True to execute."}
    return _run_argv(parts, timeout=min(timeout, 120))


# ---- log_tail ------------------------------------------------------------

def log_tail(path: str, lines: int = 100, follow: bool = False) -> dict:
    """Read the last `lines` lines of a log file. Pure stdlib. `follow` is
    accepted but ignored (a long-running follow would block the agent — agents
    should poll instead).

    Scope: files inside the active project root (gate-enforced via
    path:glob:**/*; the handler trusts the gate's scope verdict — the
    local _within_scope twin was dead code that still claimed /var/log
    was allowed, removed audit 2026-10)."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if not p.is_file():
        return {"ok": False, "error": f"not a regular file: {path}"}
    try:
        size = p.stat().st_size
        # read last ~64KB and split into lines (handles multi-GB logs cheaply)
        with open(p, "rb") as fh:
            fh.seek(max(0, size - 65536))
            chunk = fh.read().decode("utf-8", errors="replace")
        all_lines = chunk.splitlines()
        tail = all_lines[-lines:] if lines else all_lines
        return {"ok": True, "path": str(p), "lines_requested": lines,
                "lines_returned": len(tail),
                "size_bytes": size,
                "tail": "\n".join(tail)}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": path}


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        name="K8s", capability="connector.run",
        description="kubectl-equivalent: get/describe/logs/apply with plan-before-apply",
        handler=k8s, consent="per_turn",
        resources=["cwd", "connector:k8s"],
        path_args=["manifest_path", "file"],
    ))
    reg.register(Tool(
        name="LogTail", capability="Read",
        description="Read the last N lines of a service/system log file",
        handler=log_tail, consent="auto",
        resources=["path:glob:**/*"],
    ))
