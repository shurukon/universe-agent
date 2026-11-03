"""connectors/builtin/search_tools.py — Universe's search + messaging tools.

The three tools Universe ships that Universe had no counterpart for (RE map
§6): Grep (content search), Glob (pattern file search), SendMessage
(agent-to-agent message). All three stay inside the project root and the
capability gate exactly like every other builtin.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from config import get_config
from core.agent.base_agent import Tool

_MAX_RESULTS = 200
_MAX_FILE_BYTES = 2_000_000
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "dist-electron",
              ".venv", "venv", ".mypy_cache", ".pytest_cache"}


def _root() -> Path:
    try:
        from core.agent.project_context import project_root_ctx
        ctx = project_root_ctx.get()
        if ctx:
            return Path(ctx).expanduser().resolve()
    except Exception:
        pass
    configured = get_config().get("project_root", "")
    return Path(configured).expanduser().resolve() if configured else Path.cwd().resolve()


def _within(root: Any, p: Any) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _iter_files(root: Any, glob: str, include_hidden: bool = False):
    pat = (glob or "").strip() or "*"
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and (include_hidden or not d.startswith("."))]
        for name in filenames:
            if not (include_hidden or not name.startswith(".")):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
                yield full, rel


# ------------------------------------------------------------------ Grep ---
def _grep_ripgrep(pattern: str, root: Any, glob: str,
                  max_results: int) -> dict[str, Any] | None:
    exe = shutil.which("rg") or shutil.which("ripgrep")
    if not exe:
        return None
    argv = [exe, "--no-heading", "--line-number", "--color", "never",
            "--max-count", str(max_results), "--", pattern]
    if glob:
        argv[2:2] = ["--glob", glob]
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True,
                              text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = proc.stdout or ""
    matches = []
    for line in out.splitlines()[:max_results]:
        parts = line.split(":", 2)
        if len(parts) == 3:
            matches.append({"file": parts[0], "line": parts[1], "text": parts[2][:400]})
    return {"ok": True, "engine": "ripgrep", "count": len(matches),
            "truncated": len(out.splitlines()) > max_results, "matches": matches}


def grep(pattern: str = "", glob: str = "", path: str = "",
         max_results: int = 50, context_lines: int = 0,
         case_sensitive: bool = True, include_hidden: bool = False) -> dict[str, Any]:
    """Search file contents by regex (ripgrep when installed, Python walk
    otherwise). Scoped to the project root unless `path` names a subdir.

    Args:
        pattern: the regex to search for (e.g. "def get_orchestrator").
        glob: optional filename filter, e.g. "*.py" — only files whose
            name/relative path match it are searched.
        path: optional subdirectory (absolute or project-relative) to
            scope the search to; defaults to the whole project root.
        max_results: stop after this many matches (1..200, default 50).
        context_lines: accepted for wire compatibility (unused — matches
            return the matched line text).
        case_sensitive: default true; set false for case-insensitive search.
        include_hidden: include dotfiles/dotdirs in the walk.
    """
    if not pattern:
        return {"ok": False, "error": "Grep requires a pattern"}
    root = _root()
    base = root
    if path:
        # normpath returns a STR — _within expects a Path (it calls .resolve())
        cand = Path(os.path.normpath(os.path.join(str(root), path)))
        if not _within(root, cand):
            return {"ok": False, "error": "path escapes the project root"}
        base = cand
    max_results = max(1, min(int(max_results or 50), _MAX_RESULTS))
    flags = 0 if case_sensitive else re.IGNORECASE
    # context_lines is accepted for wire-compatibility; both engines return
    # line matches only, so the UI never advertises a context block it won't get
    rg = _grep_ripgrep(pattern, base, glob, max_results)
    if rg is not None:
        return rg
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return {"ok": False, "error": f"invalid regex: {e}"}
    matches: list[dict[str, Any]] = []
    truncated = False
    try:
        for full, rel in _iter_files(base, glob, include_hidden):
            try:
                if os.path.getsize(full) > _MAX_FILE_BYTES:
                    continue
                with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            matches.append({"file": rel, "line": str(i),
                                            "text": line.rstrip()[:400]})
                            if len(matches) >= max_results:
                                truncated = True
                                break
            except OSError:
                continue
            if truncated:
                break
    except re.error as e:
        return {"ok": False, "error": f"invalid regex: {e}"}
    return {"ok": True, "engine": "python", "count": len(matches),
            "truncated": truncated, "matches": matches}


# ------------------------------------------------------------------ Glob ---
def glob(pattern: str = "", path: str = "", max_results: int = 100,
         include_hidden: bool = False) -> dict[str, Any]:
    """List files matching a glob pattern (fnmatch semantics over the
    project-root walk). Scoped to the project root.

    Args:
        pattern: the glob to match against project-relative paths, e.g.
            "eval/*.py" or "**/*.md" — REQUIRED.
        path: optional subdirectory to scope the walk to; defaults to the
            project root.
        max_results: stop after this many files (1..200, default 100).
        include_hidden: include dotfiles/dotdirs in the walk.
    """
    if not pattern:
        return {"ok": False, "error": "Glob requires a pattern"}
    root = _root()
    base = root
    if path:
        # normpath returns a STR — _within expects a Path (it calls .resolve())
        cand = Path(os.path.normpath(os.path.join(str(root), path)))
        if not _within(root, cand):
            return {"ok": False, "error": "path escapes the project root"}
        base = cand
    max_results = max(1, min(int(max_results or 100), _MAX_RESULTS))
    pat = pattern.replace("\\", "/")
    files: list[str] = []
    truncated = False
    try:
        for _full, rel in _iter_files(base, "", include_hidden):
            relp = rel.replace("\\", "/")
            if fnmatch.fnmatch(relp, pat):
                files.append(relp)
                if len(files) >= max_results:
                    truncated = True
                    break
    except OSError:
        pass
    return {"ok": True, "engine": "python", "count": len(files),
            "truncated": truncated, "files": sorted(files)}


# ------------------------------------------------------------ SendMessage ---
def send_message(to: str = "", message: str = "", turn_id: str = "",
                 from_agent: str = "") -> dict[str, Any]:
    """Deliver a message to another agent's mailbox (swarm messaging).

    The message lands in the target agent's queue for its current goal; if
    the agent has no live run the message is bounced with an honest error
    rather than silently dropped.
    """
    target = (to or "").strip()
    body = (message or "").strip()
    if not target or not body:
        return {"ok": False, "error": "SendMessage needs `to` and `message`"}
    try:
        from core.agent.swarm import deliver_message
        return deliver_message(to=target, message=body, turn_id=turn_id,
                               from_agent=from_agent)
    except ImportError:
        return {"ok": False,
                "error": "agent messaging is not available in this build"}
    except Exception as e:  # honest error, never a crash
        return {"ok": False, "error": str(e)}


def register(reg) -> None:
    reg.register(Tool(
        name="Grep", capability="file.read",
        description="Search file contents by regex; ripgrep when installed, "
                    "a scoped Python walk otherwise. Returns file/line/text.",
        handler=grep, consent="auto", resources=["path:glob:**/*"]))
    reg.register(Tool(
        name="Glob", capability="file.read",
        description="List files matching a glob pattern inside the project "
                    "root. Returns relative paths.",
        handler=glob, consent="auto", resources=["path:glob:**/*"]))
    reg.register(Tool(
        name="SendMessage", capability="universe:agents",
        description="Send a message to another agent's mailbox (swarm "
                    "messaging). Bounces with an honest error when the "
                    "target has no live run.",
        handler=send_message, consent="auto", resources=["universe:agents"]))
