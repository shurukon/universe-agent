"""security/windows_sandbox.py — native Windows sandbox for shell.exec when
WSL2 isn't available (§10 extension, 2026-07).

WHERE THIS CAME FROM: OpenAI open-sourced a native Windows sandbox for Codex
(March 2026, Apache 2.0 — verified permissive, unlike the GPL-3.0 OpenHuman
reference rejected elsewhere in this project). The actual Rust source was
cloned and read directly (not guessed from docs) to build this: see
vendor/codex-windows-sandbox/ for the vendored crate and
docs/WINDOWS_SANDBOX.md for the full protocol trace this module implements.

THE DESIGN POINT THIS MODULE ENCODES: OpenAI's own fallback chain, verified
in their source (protocol/src/models.rs), is Elevated -> RestrictedToken
("unelevated") -> Disabled, where Disabled's config key is literally named
"danger-full-access" — never an automatic silent fallback. This module
mirrors that: `run_sandboxed()` tries elevated, then unelevated,
automatically. It NEVER falls through to unsandboxed execution itself —
that path is a separate, explicit-consent-gated tool
(shell_exec_unsandboxed in connectors/builtin/shell.py), matching Universe's
own CapabilityGate rather than inventing a parallel notify-and-proceed
mechanism.

VERIFICATION HONESTY, stated once here: this module was written by reading
OpenAI's actual Rust source precisely (exact IPC frame shapes, exact
CreateProcessWithLogonW sequence, exact DPAPI-based credential storage) —
not guessed. The protocol-handling logic below (frame encode/decode,
message construction) is pure Python with no Windows-specific calls and IS
covered by real, executing tests in tests/test_windows_sandbox.py, using a
mock runner process over OS-agnostic pipes so it runs in CI on Linux too.
The Windows-OS-specific glue (named pipes, CreateProcessWithLogonW, DPAPI)
is isolated in _win_* functions that import pywin32 lazily and CANNOT be
executed or verified from this development environment (no Windows
available) — those specific functions need real-machine testing on Windows
before this is trusted the way the rest of Universe has been verified.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO

from security.child_env import sanitize_env

IPC_PROTOCOL_VERSION = 4  # matches codex-windows-sandbox exactly — see
# vendor/codex-windows-sandbox/src/elevated/ipc_framed.rs
MAX_FRAME_LEN = 8 * 1024 * 1024


class WindowsSandboxLevel(str, Enum):
    ELEVATED = "elevated"
    UNELEVATED = "unelevated"      # RestrictedToken, in upstream's naming
    DISABLED = "disabled"          # upstream's own config key: "danger-full-access"


# ---------------------------------------------------------------------------
# Platform / WSL2 detection — pure Python, no Windows APIs, fully testable
# ---------------------------------------------------------------------------

def detect_platform() -> str:
    """Returns 'linux', 'macos', 'windows_wsl2', or 'windows_native'.
    WSL2 detection checks for the presence of a real `wsl.exe` AND that at
    least one distro is actually installed/runnable — a bare WSL2 feature
    flag with no distro is not usable and should fall through to native."""
    import platform as _platform
    system = _platform.system()
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"
    if system != "Windows":
        return system.lower()
    if _wsl2_usable():
        return "windows_wsl2"
    return "windows_native"


def _wsl2_usable() -> bool:
    try:
        r = subprocess.run(["wsl.exe", "--status"], capture_output=True,
                           timeout=5, text=True)
        return r.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# IPC wire protocol — length-prefixed JSON frames. Pure logic, no Windows
# calls anywhere in this section; matches ipc_framed.rs byte-for-byte
# (verified against its own test cases, see docs/WINDOWS_SANDBOX.md).
# ---------------------------------------------------------------------------

def encode_bytes(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


def decode_bytes(data: str) -> bytes:
    return base64.standard_b64decode(data.encode("ascii"))


def write_frame(stream: BinaryIO, message: dict) -> None:
    """4-byte little-endian length prefix + JSON payload — matches
    ipc_framed.rs::write_frame exactly."""
    envelope = {"version": IPC_PROTOCOL_VERSION, **message}
    payload = json.dumps(envelope).encode("utf-8")
    if len(payload) > MAX_FRAME_LEN:
        raise ValueError(f"frame too large: {len(payload)}")
    stream.write(struct.pack("<I", len(payload)))
    stream.write(payload)
    stream.flush()


def read_frame(stream: BinaryIO, timeout_check=None) -> dict | None:
    """Returns None on clean EOF, matching ipc_framed.rs::read_frame."""
    len_buf = stream.read(4)
    if not len_buf:
        return None
    if len(len_buf) != 4:
        raise IOError("truncated frame length prefix")
    (length,) = struct.unpack("<I", len_buf)
    if length > MAX_FRAME_LEN:
        raise ValueError(f"frame too large: {length}")
    payload = stream.read(length)
    if len(payload) != length:
        raise IOError("truncated frame payload")
    return json.loads(payload)


def build_spawn_request(*, command: list[str], cwd: str, env: dict[str, str],
                        codex_home: str, real_codex_home: str,
                        network_enabled: bool = False,
                        timeout_ms: int | None = None,
                        writable_roots: list[str] | None = None) -> dict:
    """Builds a spawn_request frame body matching SpawnRequest's exact JSON
    shape (protocol/src/models.rs + ipc_framed.rs). `writable_roots=None`
    means Unrestricted filesystem within the managed profile — the simplest
    valid PermissionProfile variant; per-path Restricted entries (matching
    ManagedFileSystemPermissions::Restricted) are a documented follow-up,
    not implemented in this first pass."""
    file_system = (
        {"type": "unrestricted"} if writable_roots is None else
        {"type": "restricted", "entries": [
            {"path": p, "access": "write"} for p in writable_roots]}
    )
    return {
        "type": "spawn_request",
        "payload": {
            "command": command,
            "cwd": cwd,
            "env": env,
            "permission_profile": {
                "type": "managed",
                "file_system": file_system,
                "network": "enabled" if network_enabled else "restricted",
            },
            "workspace_roots": writable_roots or [cwd],
            "codex_home": codex_home,
            "real_codex_home": real_codex_home,
            "cap_sids": [],
            "timeout_ms": timeout_ms,
            "tty": False,
            "stdin_open": False,
            "use_private_desktop": False,
        },
    }


@dataclass
class SandboxRunResult:
    ok: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None
    level_used: str | None = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "returncode": self.exit_code, "stdout": self.stdout,
                "stderr": self.stderr, "timed_out": self.timed_out,
                "error": self.error, "sandboxed": True, "sandbox_level": self.level_used}


def _read_frame_bg(read_stream: BinaryIO, frame_queue: "queue.Queue") -> None:
    """Runs in a background thread, pushing (kind, value) pairs onto
    frame_queue forever. Exists because a plain blocking .read(n) call has
    NO built-in timeout on either a POSIX pipe or a Windows named-pipe
    handle — confirmed by direct reproduction while building this: a
    silent-but-open pipe blocks a bare read() indefinitely. Running the
    blocking read on a background thread lets the CALLER enforce a real
    wall-clock timeout via queue.get(timeout=...), regardless of platform,
    without needing select()-style readiness polling that doesn't work
    uniformly across POSIX pipes and Win32 pipe HANDLEs anyway."""
    while True:
        try:
            msg = read_frame(read_stream)
        except Exception as e:
            frame_queue.put(("error", e))
            return
        frame_queue.put(("frame", msg))
        if msg is None:
            return  # clean EOF — nothing more will ever arrive


def _drive_protocol(read_stream: BinaryIO, write_stream: BinaryIO,
                    spawn_request: dict, timeout_s: float) -> SandboxRunResult:
    """Pure protocol driver: write spawn_request, read spawn_ready, then
    read output/exit/error frames until Exit or the stream closes. Takes
    plain binary streams — a real named pipe on Windows, or (for tests) a
    plain os.pipe()/socket pair — so this exact function is what
    tests/test_windows_sandbox.py exercises against a mock runner, and
    what the real Windows path calls too. No platform-specific code here.

    Reads happen on a background thread (see _read_frame_bg) so a genuine
    hang — the runner or the sandboxed process itself going silent while
    keeping the pipe open — is bounded by timeout_s. A first version of
    this function checked the deadline only *between* blocking reads,
    which does nothing against a read that never returns; caught by an
    actual hang test, not by inspection."""
    write_frame(write_stream, spawn_request)

    frame_queue: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_read_frame_bg, args=(read_stream, frame_queue), daemon=True)
    reader.start()

    def _next(deadline: float) -> tuple[str, Any]:
        remaining = max(0.0, deadline - time.time())
        try:
            return frame_queue.get(timeout=remaining)
        except queue.Empty:
            return ("timeout", None)

    deadline = time.time() + timeout_s
    kind, ready = _next(deadline)
    if kind == "timeout":
        return SandboxRunResult(ok=False, timed_out=True, error="timed out waiting for spawn_ready")
    if kind == "error":
        return SandboxRunResult(ok=False, error=f"reading spawn_ready failed: {ready}")
    if ready is None:
        return SandboxRunResult(ok=False, error="runner closed before spawn_ready")
    if ready.get("type") == "error":
        p = ready.get("payload", {})
        return SandboxRunResult(ok=False, error=f"{p.get('stage')}: {p.get('message')}")
    if ready.get("type") != "spawn_ready":
        return SandboxRunResult(ok=False, error=f"unexpected first message: {ready.get('type')}")

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    while True:
        kind, msg = _next(deadline)
        if kind == "timeout":
            return SandboxRunResult(ok=False, timed_out=True,
                                    stdout=b"".join(stdout_chunks).decode("utf-8", "replace"),
                                    stderr=b"".join(stderr_chunks).decode("utf-8", "replace"),
                                    error="client-side timeout waiting on runner")
        if kind == "error":
            return SandboxRunResult(ok=False, error=f"reading runner stream failed: {msg}",
                                    stdout=b"".join(stdout_chunks).decode("utf-8", "replace"),
                                    stderr=b"".join(stderr_chunks).decode("utf-8", "replace"))
        if msg is None:
            return SandboxRunResult(ok=False, error="runner closed mid-stream",
                                    stdout=b"".join(stdout_chunks).decode("utf-8", "replace"),
                                    stderr=b"".join(stderr_chunks).decode("utf-8", "replace"))
        mtype = msg.get("type")
        payload = msg.get("payload", {})
        if mtype == "output":
            data = decode_bytes(payload["data_b64"])
            (stdout_chunks if payload.get("stream") == "stdout" else stderr_chunks).append(data)
        elif mtype == "exit":
            return SandboxRunResult(
                ok=(payload.get("exit_code") == 0 and not payload.get("timed_out")),
                exit_code=payload.get("exit_code"), timed_out=bool(payload.get("timed_out")),
                stdout=b"".join(stdout_chunks).decode("utf-8", "replace"),
                stderr=b"".join(stderr_chunks).decode("utf-8", "replace"))
        elif mtype == "error":
            return SandboxRunResult(ok=False, error=f"{payload.get('stage')}: {payload.get('message')}",
                                    stdout=b"".join(stdout_chunks).decode("utf-8", "replace"),
                                    stderr=b"".join(stderr_chunks).decode("utf-8", "replace"))
        # unknown message types are ignored rather than treated as fatal —
        # forward compatibility with a newer protocol version on the runner
        # side, matching the spirit of the upstream version field.


# ---------------------------------------------------------------------------
# Windows-OS-specific glue. Every function below requires pywin32 and a real
# Windows machine to execute — none of this can run or be verified in this
# development environment. Written from direct source reading (see class
# docstring), not guessed, but flagged here explicitly rather than implied
# to carry the same verification confidence as the rest of this module.
# ---------------------------------------------------------------------------

def _win_load_credentials(codex_home: Path, network_enabled: bool) -> tuple[str, str]:
    """Reads sandbox_users.json + setup_marker.json (written by the vendored
    codex-windows-sandbox-setup.exe) and DPAPI-unprotects the chosen
    (online/offline) user's password. Matches identity.rs::select_identity
    and decode_password exactly — same on-disk format, same DPAPI call."""
    import win32crypt  # pywin32 — Windows only

    marker_path = codex_home / "windows_sandbox" / "setup_marker.json"
    users_path = codex_home / "windows_sandbox" / "secrets" / "sandbox_users.json"
    json.loads(marker_path.read_text())
    users = json.loads(users_path.read_text())
    record = users["online"] if network_enabled else users["offline"]
    blob = base64.standard_b64decode(record["password"])
    # CryptUnprotectData returns (description, data); we only need data.
    _, decrypted = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return record["username"], decrypted.decode("utf-8")


def _win_run_via_runner(*, runner_exe: Path, username: str, password: str,
                        cwd: str, spawn_request: dict, timeout_s: float) -> SandboxRunResult:
    """Creates the two named pipes (ACL'd to `username` only), launches
    codex-command-runner.exe under that low-priv user via
    CreateProcessWithLogonW with --pipe-in/--pipe-out pointing at them, then
    hands the connected pipe handles to _drive_protocol() — the same
    protocol driver the tests exercise against a mock. Matches
    runner_client.rs::spawn_runner_transport."""
    import win32api
    import win32con
    import win32file
    import win32pipe
    import win32process
    import win32security
    import pywintypes

    pipe_in_name = rf"\\.\pipe\universe-sandbox-{os.getpid()}-{time.time_ns()}-in"
    pipe_out_name = rf"\\.\pipe\universe-sandbox-{os.getpid()}-{time.time_ns()}-out"

    # Restrict each pipe via a REAL explicit DACL — an empty
    # SECURITY_DESCRIPTOR inherits the default DACL, which would let any
    # local user connect to the sandbox transport. Explicit entries:
    # the creating user and Administrators get full access; EVERYONE gets
    # nothing (the sandbox runner connects as the low-priv sandbox user,
    # which CreateProcessWithLogonW grants pipe access via the same token).
    creator = win32security.LookupAccountName("", "CURRENT_USER")
    admins = win32security.LookupAccountName("", "Administrators")
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION_DIR, win32con.GENERIC_ALL, creator[0])
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION_DIR, win32con.GENERIC_ALL, admins[0])
    sa = win32security.SECURITY_DESCRIPTOR()
    sa.Initialize()
    sa.SetSecurityDescriptorOwner(creator[0], False)
    sa.SetSecurityDescriptorDacl(True, dacl, False)  # explicit, protected DACL
    sd = win32security.SECURITY_ATTRIBUTES()
    sd.SECURITY_DESCRIPTOR = sa

    h_in = win32pipe.CreateNamedPipe(
        pipe_in_name, win32con.PIPE_ACCESS_OUTBOUND,
        win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_WAIT, 1, 65536, 65536, 0, sd)
    h_out = win32pipe.CreateNamedPipe(
        pipe_out_name, win32con.PIPE_ACCESS_INBOUND,
        win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_WAIT, 1, 65536, 65536, 0, sd)

    runner_cmdline = (f'"{runner_exe}" "--pipe-in={pipe_in_name}" '
                      f'"--pipe-out={pipe_out_name}"')
    startup_info = win32process.STARTUPINFO()
    try:
        _, _, pid, _tid = win32process.CreateProcessWithLogonW(
            username, ".", password, win32process.LOGON_WITH_PROFILE,
            str(runner_exe), runner_cmdline,
            win32con.CREATE_NO_WINDOW | win32con.CREATE_UNICODE_ENVIRONMENT,
            None, cwd, startup_info)
    except pywintypes.error as e:
        win32file.CloseHandle(h_in)
        win32file.CloseHandle(h_out)
        return SandboxRunResult(ok=False, error=f"CreateProcessWithLogonW failed: {e}")

    try:
        win32pipe.ConnectNamedPipe(h_in, None)
        win32pipe.ConnectNamedPipe(h_out, None)
    except pywintypes.error as e:
        win32api.TerminateProcess(pid, 1)
        win32file.CloseHandle(h_in)
        win32file.CloseHandle(h_out)
        return SandboxRunResult(ok=False, error=f"pipe connect handshake failed: {e}")

    # win32file.PyHANDLE objects support the same read/write protocol our
    # pure _drive_protocol() expects via a small adapter, since PyHANDLE
    # isn't itself a Python file object.
    read_stream = _PipeReadAdapter(h_out)
    write_stream = _PipeWriteAdapter(h_in)
    result: SandboxRunResult
    try:
        result = _drive_protocol(read_stream, write_stream, spawn_request, timeout_s)
    finally:
        # kill the runner on OUR side giving up (timeout / protocol error):
        # a hung runner under the sandbox user must not outlive the call
        try:
            win32api.TerminateProcess(pid, 1)
        except pywintypes.error:
            pass  # already exited on its own
        win32file.CloseHandle(h_in)
        win32file.CloseHandle(h_out)
    return result


class _PipeReadAdapter:
    """Thin adapter so a Win32 pipe HANDLE satisfies the plain
    read(n)->bytes interface _drive_protocol() expects — keeps the protocol
    driver itself free of any pywin32 dependency, so it stays testable on
    any platform."""
    def __init__(self, handle):
        self._h = handle

    def read(self, n: int) -> bytes:
        # named pipes may return FEWER than n bytes per ReadFile call; a
        # single call could hand the protocol a truncated frame header
        import win32file
        chunks: list[bytes] = []
        remaining = int(n)
        while remaining > 0:
            _, data = win32file.ReadFile(self._h, remaining)
            if not data:
                break  # EOF / pipe closed
            chunks.append(data)
            remaining -= len(data)
        return b"".join(chunks)


class _PipeWriteAdapter:
    def __init__(self, handle):
        self._h = handle

    def write(self, data: bytes) -> None:
        import win32file
        win32file.WriteFile(self._h, data)

    def flush(self) -> None:
        pass  # WriteFile on a named pipe is unbuffered from our side


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def run_sandboxed(command: str, *, cwd: str = ".", env: dict | None = None,
                  timeout: int = 120, codex_home: str | None = None,
                  vendor_dir: str | None = None) -> dict[str, Any]:
    """Runs `command` inside the strongest available Windows sandbox level,
    trying elevated first and automatically falling back to unelevated —
    mirroring upstream's own fallback chain exactly. NEVER falls through to
    fully unsandboxed execution on its own; that is a separate,
    explicit-consent-gated tool (shell_exec_unsandboxed in
    connectors/builtin/shell.py), not a code path in this function.
    Returns the same {"ok", "returncode", "stdout", "stderr", ...} shape as
    security/sandbox.py's Docker-based run_sandboxed for a consistent
    caller-side contract regardless of which sandbox backend is in play.
    """
    home = Path(codex_home or (Path.home() / ".universe"))
    vendor = Path(vendor_dir or (Path(__file__).parent.parent / "vendor" / "codex-windows-sandbox" / "bin"))
    runner_exe = vendor / "codex-command-runner.exe"
    setup_exe = vendor / "codex-windows-sandbox-setup.exe"

    if not runner_exe.exists() or not setup_exe.exists():
        return SandboxRunResult(
            ok=False, error=f"vendored sandbox binaries not found under {vendor} — "
                           f"see docs/WINDOWS_SANDBOX.md to build them on Windows first"
        ).to_dict()

    for level, network_enabled in ((WindowsSandboxLevel.ELEVATED, False),
                                   (WindowsSandboxLevel.UNELEVATED, False)):
        try:
            ensured = _ensure_setup(setup_exe, home, level)
        except Exception:
            continue  # this level's setup failed — try the next, weaker one
        if not ensured:
            continue
        try:
            username, password = _win_load_credentials(home, network_enabled)
        except Exception:
            continue
        spawn_request = build_spawn_request(
            command=["cmd.exe", "/c", command], cwd=cwd,
            # merge the caller's vars over a SANITIZED parent environment —
            # the sandboxed child is still agent-controlled, so
            # UNIVERSE_BRIDGE_TOKEN / UNIVERSE_PASSPHRASE must never reach it
            # (one approved command could otherwise self-approve every later
            # explicit consent via the bridge API). Passing ONLY the caller's
            # dict used to strip PATH/SYSTEMROOT etc. and make every child
            # process fail to start.
            env=sanitize_env(env),
            codex_home=str(home), real_codex_home=str(home),
            network_enabled=network_enabled, timeout_ms=timeout * 1000)
        result = _win_run_via_runner(runner_exe=runner_exe, username=username,
                                     password=password, cwd=cwd,
                                     spawn_request=spawn_request, timeout_s=float(timeout))
        result.level_used = level.value
        if result.ok or result.exit_code is not None:
            # got a real execution result (even a non-zero exit code counts
            # as the sandbox working correctly) — don't silently degrade to
            # a weaker level just because the COMMAND itself failed.
            return result.to_dict()
        # else: this level's plumbing itself failed (not the command) — try
        # the next, weaker level automatically, matching upstream's chain.

    return SandboxRunResult(
        ok=False, error="both elevated and unelevated sandbox setup failed — "
                       "use shell_exec_unsandboxed (requires explicit consent) "
                       "if you need to proceed without a sandbox"
    ).to_dict()


def _ensure_setup(setup_exe: Path, codex_home: Path, level: WindowsSandboxLevel) -> bool:
    """Invokes the vendored setup binary for the given level. Returns True
    only on a clean success exit code — any failure (missing elevation,
    policy denial, etc.) returns False so run_sandboxed() moves to the next
    weaker level rather than raising."""
    try:
        r = subprocess.run(
            [str(setup_exe), f"--level={level.value}", f"--codex-home={codex_home}"],
            capture_output=True, timeout=60, text=True)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
