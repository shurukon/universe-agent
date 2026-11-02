"""connectors/builtin/git.py — git operations (read auto-allowed, write prompts).

All subprocess calls use argv lists (shell=False) — the old f-string
command construction let a `limit` of "1 && calc.exe" or a commit message
containing `"` execute arbitrary shell.
"""
from __future__ import annotations

import subprocess
import time

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry


def _repo_arg(repo: str = ".", cwd: str = "", path: str = "") -> str:
    return str(cwd or path or repo or ".")


def _git(args: list[str], cwd: str, timeout: int = 60) -> dict:
    """Run git with an argv list — no shell, no injection surface."""
    start = time.time()
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "elapsed_ms": int((time.time() - start) * 1000),
            "cmd": " ".join(args),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "cmd": " ".join(args),
                "elapsed_ms": int((time.time() - start) * 1000)}


def git_status(repo: str = ".", cwd: str = "", path: str = "") -> dict:
    return _git(["status", "--porcelain"], cwd=_repo_arg(repo, cwd, path))


def git_diff(repo: str = ".", staged: bool = True, cwd: str = "", path: str = "") -> dict:
    cmd = ["diff", "--cached"] if staged else ["diff"]
    return _git(cmd, cwd=_repo_arg(repo, cwd, path))


def git_log(repo: str = ".", limit: int = 20, cwd: str = "", path: str = "") -> dict:
    try:
        limit = max(1, min(int(str(limit).strip()), 500))
    except (TypeError, ValueError):
        limit = 20  # forgiving default
    return _git(["log", "--oneline", f"-{limit}"], cwd=_repo_arg(repo, cwd, path))


def git_commit(repo: str = ".", message: str = "", cwd: str = "", path: str = "",
               msg: str = "", files: list[str] | None = None) -> dict:
    repo = _repo_arg(repo, cwd, path)
    message = str(msg or message)
    if not message.strip():
        message = "Update via UniverseAgent"
    # explicit `files` stage exactly those; the default add -A sweeps every
    # dirty file in the worktree — the result now also lists what was
    # actually staged so the model sees what landed in the commit
    if files:
        add = _git(["add", "--", *[str(f) for f in files]], cwd=repo)
    else:
        add = _git(["add", "-A"], cwd=repo)
    if not add.get("ok"):
        return add
    staged = _git(["diff", "--cached", "--name-only"], cwd=repo)
    staged_files = staged.get("stdout", "").splitlines() if staged.get("ok") else []
    out = _git(["commit", "-m", message], cwd=repo)
    if isinstance(out, dict):
        out["staged_files"] = staged_files
        if not files and staged_files:
            out["note"] = "no `files` argument: staged every dirty file (add -A)"
    return out


def git_push(repo: str = ".", remote: str = "origin", branch: str = "",
             cwd: str = "", path: str = "") -> dict:
    remote = str(remote or "origin")
    if not remote.replace("-", "").replace("_", "").isalnum():
        return {"ok": False, "error": "invalid remote name"}
    args = ["push", remote]
    if branch:
        # branch is a ref name: reject anything that could smuggle flags
        if branch.startswith("-"):
            return {"ok": False, "error": "invalid branch name"}
        args.append(str(branch))
    return _git(args, cwd=_repo_arg(repo, cwd, path))


def register(reg: ToolRegistry) -> None:
    # `repo` here IS a filesystem path (the git working dir) — declared so
    # the gate scope-checks it, while GitHub's owner/repo `repo` string
    # (thirdparty.py) is not a path and needs no declaration.
    _git_path_args = ["repo", "cwd", "path"]
    reg.register(Tool(name="GitStatus", capability="git.read",
                      description="Show git status", handler=git_status, consent="auto",
                      resources=["cwd"], path_args=_git_path_args))
    reg.register(Tool(name="GitDiff", capability="git.read",
                      description="Show git diff", handler=git_diff, consent="auto",
                      resources=["cwd"], path_args=_git_path_args))
    reg.register(Tool(name="GitLog", capability="git.read",
                      description="Show git log", handler=git_log, consent="auto",
                      resources=["cwd"], path_args=_git_path_args))
    reg.register(Tool(name="GitCommit", capability="git.write",
                      description="Stage all + commit", handler=git_commit, consent="per_turn",
                      resources=["cwd"], path_args=_git_path_args))
    reg.register(Tool(name="GitPush", capability="git.write",
                      description="Push to remote (prompts first time per goal)",
                      handler=git_push, consent="per_turn", resources=["cwd"],
                      path_args=_git_path_args))
