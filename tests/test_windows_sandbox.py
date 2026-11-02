"""tests/test_windows_sandbox.py — tests for security/windows_sandbox.py.

Two tiers of coverage, matching the module's own honesty about what can
and can't be verified from this (Linux) development environment:

1. PROTOCOL LOGIC — genuinely executed, not mocked-around. A mock "runner"
   thread speaks the exact same length-prefixed JSON frames over a real
   os.pipe() pair (works identically on Linux and Windows — pipes are not
   Windows-specific, only the NAMED pipe + CreateProcessWithLogonW launch
   sequence is). This exercises write_frame/read_frame/_drive_protocol
   end-to-end for real, which is what would have caught a framing bug the
   same way live-testing caught the MCP protocol bugs earlier in this
   project.

2. WINDOWS-OS-SPECIFIC GLUE (_win_load_credentials, _win_run_via_runner,
   the pywin32 named-pipe/CreateProcessWithLogonW code) — NOT executable
   here at all (no Windows, no pywin32). These are explicitly not covered
   by this test file; see docs/WINDOWS_SANDBOX.md for what still needs
   real-machine verification before this is trusted.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from security.windows_sandbox import (
    build_spawn_request, decode_bytes, detect_platform, encode_bytes,
    read_frame, write_frame, _drive_protocol, _wsl2_usable,
)


# ---------------------------------------------------------------------------
# Frame encode/decode — matches ipc_framed.rs's own round-trip test
# ---------------------------------------------------------------------------

def test_frame_round_trip():
    import io
    buf = io.BytesIO()
    write_frame(buf, {"type": "output", "payload": {
        "data_b64": encode_bytes(b"hello"), "stream": "stdout"}})
    buf.seek(0)
    decoded = read_frame(buf)
    assert decoded["version"] == 4
    assert decoded["type"] == "output"
    assert decode_bytes(decoded["payload"]["data_b64"]) == b"hello"


def test_read_frame_returns_none_on_clean_eof():
    import io
    assert read_frame(io.BytesIO(b"")) is None


def test_read_frame_rejects_oversized_frame():
    import io
    import struct
    buf = io.BytesIO(struct.pack("<I", 999_999_999))
    with pytest.raises(ValueError):
        read_frame(buf)


def test_spawn_request_shape_matches_upstream_exactly():
    """Mirrors ipc_framed.rs's own spawn_request_serializes_permission_profile
    test — same key assertions, same 'managed' tag, same absent legacy keys."""
    req = build_spawn_request(
        command=["cmd.exe", "/c", "ver"], cwd=r"C:\workspace", env={},
        codex_home=r"C:\codex", real_codex_home=r"C:\Users\codex",
        timeout_ms=1000)
    assert req["type"] == "spawn_request"
    p = req["payload"]
    assert p["permission_profile"]["type"] == "managed"
    assert p["command"] == ["cmd.exe", "/c", "ver"]
    assert p["cwd"] == r"C:\workspace"
    assert p["tty"] is False
    assert p["timeout_ms"] == 1000
    assert "policy_json_or_preset" not in p
    assert "sandbox_policy_cwd" not in p


def test_spawn_request_network_flag_maps_to_restricted_or_enabled():
    req_off = build_spawn_request(command=["x"], cwd=".", env={},
                                  codex_home=".", real_codex_home=".",
                                  network_enabled=False)
    req_on = build_spawn_request(command=["x"], cwd=".", env={},
                                 codex_home=".", real_codex_home=".",
                                 network_enabled=True)
    assert req_off["payload"]["permission_profile"]["network"] == "restricted"
    assert req_on["payload"]["permission_profile"]["network"] == "enabled"


# ---------------------------------------------------------------------------
# Full protocol driver, against a real mock runner thread over os.pipe()
# ---------------------------------------------------------------------------

class _MockRunner:
    """Speaks the exact runner side of the protocol using the SAME
    write_frame/read_frame functions as the real client — a scripted
    sequence of responses, running in a background thread over a real
    os.pipe() pair (no mocking of the framing itself)."""

    def __init__(self, script, hang_after_script=False):
        self.script = script  # list of message dicts to send after spawn_request
        # hang_after_script=True keeps the write end OPEN and silent instead
        # of closing it once the script is exhausted — needed to genuinely
        # test a client-side timeout. Closing immediately (the default)
        # produces a clean EOF instead, which is a different, already-
        # handled code path — an earlier version of this test closed
        # unconditionally and silently tested the wrong scenario.
        self.hang_after_script = hang_after_script
        # pipe A: client writes -> runner reads  (this is "pipe-in" upstream)
        # pipe B: runner writes -> client reads  (this is "pipe-out" upstream)
        self._a_r, self._a_w = os.pipe()
        self._b_r, self._b_w = os.pipe()
        self.client_read = os.fdopen(self._b_r, "rb")
        self.client_write = os.fdopen(self._a_w, "wb")
        self._runner_read = os.fdopen(self._a_r, "rb")
        self._runner_write = os.fdopen(self._b_w, "wb")
        self.received_spawn_request = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        try:
            self.received_spawn_request = read_frame(self._runner_read)
            for msg in self.script:
                write_frame(self._runner_write, msg)
                time.sleep(0.01)
            if self.hang_after_script:
                time.sleep(30)  # simulate a genuine hang; the test's own
                                # timeout (much shorter) must fire first
        except Exception:
            pass
        finally:
            if not self.hang_after_script:
                try:
                    self._runner_write.close()
                except Exception:
                    pass

    def join(self, timeout=5):
        self._thread.join(timeout)


def _output(text: str, stream: str = "stdout") -> dict:
    return {"type": "output", "payload": {"data_b64": encode_bytes(text.encode()), "stream": stream}}


def test_drive_protocol_success_end_to_end():
    runner = _MockRunner(script=[
        {"type": "spawn_ready", "payload": {"process_id": 4242}},
        _output("hello "),
        _output("world\n"),
        _output("uh oh\n", stream="stderr"),
        {"type": "exit", "payload": {"exit_code": 0, "timed_out": False}},
    ])
    runner.start()
    req = build_spawn_request(command=["echo", "hi"], cwd=".", env={},
                              codex_home=".", real_codex_home=".")
    result = _drive_protocol(runner.client_read, runner.client_write, req, timeout_s=5)
    runner.join()

    assert runner.received_spawn_request["type"] == "spawn_request"
    assert result.ok is True
    assert result.exit_code == 0
    assert result.stdout == "hello world\n"
    assert result.stderr == "uh oh\n"
    assert result.timed_out is False


def test_drive_protocol_nonzero_exit_is_not_ok_but_not_an_error():
    runner = _MockRunner(script=[
        {"type": "spawn_ready", "payload": {"process_id": 1}},
        _output("boom\n", stream="stderr"),
        {"type": "exit", "payload": {"exit_code": 1, "timed_out": False}},
    ])
    runner.start()
    req = build_spawn_request(command=["false"], cwd=".", env={},
                              codex_home=".", real_codex_home=".")
    result = _drive_protocol(runner.client_read, runner.client_write, req, timeout_s=5)
    runner.join()

    assert result.ok is False
    assert result.exit_code == 1
    assert result.error is None  # a failing COMMAND is not a sandbox ERROR
    assert result.stderr == "boom\n"


def test_drive_protocol_runner_reports_spawn_error():
    runner = _MockRunner(script=[
        {"type": "error", "payload": {"message": "CreateProcessAsUserW failed",
                                       "stage": "spawn_child", "windows_error_code": 1312}},
    ])
    runner.start()
    req = build_spawn_request(command=["x"], cwd=".", env={},
                              codex_home=".", real_codex_home=".")
    result = _drive_protocol(runner.client_read, runner.client_write, req, timeout_s=5)
    runner.join()

    assert result.ok is False
    assert "spawn_child" in result.error
    assert "CreateProcessAsUserW" in result.error


def test_drive_protocol_runner_closes_before_spawn_ready():
    runner = _MockRunner(script=[])  # closes immediately, no spawn_ready ever sent
    runner.start()
    req = build_spawn_request(command=["x"], cwd=".", env={},
                              codex_home=".", real_codex_home=".")
    result = _drive_protocol(runner.client_read, runner.client_write, req, timeout_s=5)
    runner.join()

    assert result.ok is False
    assert "spawn_ready" in result.error


def test_drive_protocol_client_side_timeout():
    """If the runner goes silent mid-stream WHILE KEEPING THE PIPE OPEN
    (hang_after_script=True — distinct from a clean close, which is a
    different, already-covered code path), the client must time out
    rather than block forever — important since a hung sandboxed process
    must not hang the whole agent. This is what caught the real bug: the
    original _drive_protocol only checked the deadline *between* blocking
    reads, which does nothing once a read is actually in progress."""
    runner = _MockRunner(script=[
        {"type": "spawn_ready", "payload": {"process_id": 1}},
        # deliberately never sends 'exit', and hang_after_script keeps the
        # pipe open (no EOF) so this genuinely exercises the timeout path
    ], hang_after_script=True)
    runner.start()
    req = build_spawn_request(command=["sleep", "999"], cwd=".", env={},
                              codex_home=".", real_codex_home=".")
    result = _drive_protocol(runner.client_read, runner.client_write, req, timeout_s=0.3)
    assert result.ok is False
    assert result.timed_out is True


def test_drive_protocol_unknown_message_type_is_ignored_not_fatal():
    """Forward compatibility: an unrecognized message type from a newer
    runner version should be skipped, not treated as a fatal error."""
    runner = _MockRunner(script=[
        {"type": "spawn_ready", "payload": {"process_id": 1}},
        {"type": "some_future_message_type", "payload": {"whatever": True}},
        _output("still works\n"),
        {"type": "exit", "payload": {"exit_code": 0, "timed_out": False}},
    ])
    runner.start()
    req = build_spawn_request(command=["x"], cwd=".", env={},
                              codex_home=".", real_codex_home=".")
    result = _drive_protocol(runner.client_read, runner.client_write, req, timeout_s=5)
    runner.join()

    assert result.ok is True
    assert result.stdout == "still works\n"


# ---------------------------------------------------------------------------
# Platform / WSL2 detection
# ---------------------------------------------------------------------------

def test_detect_platform_matches_the_host():
    """detect_platform returns the actual host it runs on — the desktop v1
    is built and shipped on Windows (windows_wsl2/native), CI may be linux."""
    import sys
    host = detect_platform()
    if sys.platform.startswith("win"):
        assert host.startswith("windows")
    elif sys.platform.startswith("linux"):
        assert host == "linux"
    else:
        assert host == sys.platform


def test_wsl2_usable_false_when_wsl_exe_missing(monkeypatch):
    def fake_run(*a, **k):
        raise FileNotFoundError("no such file: wsl.exe")
    monkeypatch.setattr("subprocess.run", fake_run)
    assert _wsl2_usable() is False


def test_wsl2_usable_true_when_wsl_status_succeeds(monkeypatch):
    class FakeResult:
        returncode = 0
    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeResult())
    assert _wsl2_usable() is True


def test_wsl2_usable_false_on_timeout(monkeypatch):
    import subprocess as sp
    def fake_run(*a, **k):
        raise sp.TimeoutExpired(cmd="wsl.exe", timeout=5)
    monkeypatch.setattr("subprocess.run", fake_run)
    assert _wsl2_usable() is False


# ---------------------------------------------------------------------------
# run_sandboxed() guard clause — this much genuinely runs the same on any
# platform, since the "are the vendored binaries present" check happens
# before any Windows-specific code path.
# ---------------------------------------------------------------------------

def test_run_sandboxed_reports_missing_vendored_binaries_clearly(tmp_path):
    from security.windows_sandbox import run_sandboxed
    result = run_sandboxed("echo hi", vendor_dir=str(tmp_path / "nonexistent"))
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert "WINDOWS_SANDBOX.md" in result["error"]
