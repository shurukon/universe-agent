"""tests/e2e/test_cli_subprocess_e2e.py — TRUE end-to-end: the `universe`
CLI as a real subprocess, driven over stdin, against a real on-disk home.

The in-process e2e (test_session_e2e.py) drives the stack through Python
calls; this module proves the SAME session contract survives the actual
binary boundary: process spawn → REPL dispatch → SessionStore files → a
SECOND process resuming what the first one wrote.

Run:  python -m pytest tests/e2e/test_cli_subprocess_e2e.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli(home: Path, *lines: str, timeout: int = 240) -> subprocess.CompletedProcess:
    """Feed `lines` to the CLI stdin and capture the full session."""
    env = dict(os.environ)
    env["UNIVERSE_HOME"] = str(home)
    env["UNIVERSE_BRIDGE_TOKEN"] = "dev"
    env["GAX_ENGINE"] = "session"
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("GAX_OTEL_ENDPOINT", None)          # no telemetry in tests
    return subprocess.run(
        [sys.executable, "-m", "cli"],
        input="\n".join(lines) + "\n",
        text=True, capture_output=True,
        env=env, timeout=timeout, cwd=str(REPO_ROOT),
    )


def test_session_trio_live_in_one_process(tmp_path):
    """/new → /sessions → /fork → /sessions → /compact, end to end."""
    r = _run_cli(tmp_path,
                 "/new", "/sessions", "/fork", "/sessions", "/compact",
                 "quit")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "New session:" in out, out
    assert "1 session(s)" in out, out
    assert "Forked" in out, out
    assert "2 session(s)" in out, out
    # the fork's transcript is a copy of an empty parent — honest refusal
    assert "nothing to compact" in out, out
    assert "Goodbye" in out, out


def test_session_outlives_the_process(tmp_path):
    """Persistence across the binary boundary: process 1 creates a session,
    process 2 switches to it by id and sees it in the same on-disk home."""
    r1 = _run_cli(tmp_path, "/new", "quit")
    assert r1.returncode == 0, r1.stdout + r1.stderr
    out1 = r1.stdout + r1.stderr
    sid = next(w for w in out1.split() if w.startswith("sess_"))
    # the on-disk artifacts exist where SessionStore promised
    assert (tmp_path / "sessions" / sid / "transcript.jsonl").exists()
    assert (tmp_path / "sessions" / sid / "meta.json").exists()
    r2 = _run_cli(tmp_path, f"/sessions {sid}", "/sessions", "quit")
    out2 = r2.stdout + r2.stderr
    assert r2.returncode == 0, out2
    assert f"Switched to session {sid}" in out2, out2
    assert "2 session(s)" not in out2, "process 2 must not mint extra rows"
    assert f"{sid}" in out2, out2


def test_help_lists_session_commands(tmp_path):
    """The README-promised trio shows up in /help."""
    r = _run_cli(tmp_path, "/help", "quit")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    for name in ("/new", "/fork", "/compact", "/sessions"):
        assert name in out, f"/help must list {name}"
