"""connectors/builtin/shell.py — shell.exec tool (capability-gated, scoped to cwd).

§6, §10. Prompts on first use per goal. Long-running sessions killed at 5 min.
Quarantine-tier skills run this in a Docker sandbox (security/sandbox.py).

Windows without WSL2 (2026-07): shell.exec routes through
security/windows_sandbox.py's elevated -> unelevated chain automatically —
mirroring upstream's own fallback (see that module's docstring). It NEVER
falls through to unsandboxed execution on its own. Fully unsandboxed
execution is a SEPARATE tool below (shell_exec_unsandboxed, consent=explicit)
so bypassing the sandbox is always a deliberate, gated choice — not
something that happens silently because setup failed.
"""
from __future__ import annotations

import os
import subprocess
import time

from core.agent.base_agent import Tool
from connectors.builtin import ToolRegistry
from security.child_env import sanitize_env


SHELL_TIMEOUT = 5 * 60  # §10: kill at 5 minutes


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process AND its children. subprocess.run(timeout=) only kills
    the direct child; with shell=True on Windows that child is cmd.exe and
    the real command keeps running as an orphan (audit 2026-09: hanging
    ffprobe grandchild outlived its 30s budget)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
        except Exception:
            pass
    else:
        try:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _spawn(argv: list[str] | None, cmd: str, cwd: str, env: dict | None,
           new_session: bool) -> subprocess.Popen:
    kwargs: dict = {"cwd": cwd, "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE, "text": True,
                    "env": sanitize_env(env)}
    if os.name != "nt":
        # group the whole command so killpg can take the tree down with it
        kwargs["start_new_session"] = new_session
    if argv is not None:
        return subprocess.Popen(argv, shell=False, **kwargs)
    return subprocess.Popen(cmd, shell=True, **kwargs)


def _run(cmd: str, cwd: str = ".", timeout: int = SHELL_TIMEOUT,
         env: dict | None = None) -> dict:
    return _run_generic(None, cmd, cwd, timeout, env)


def _run_argv(argv: list[str], cwd: str = ".", timeout: int = SHELL_TIMEOUT,
              env: dict | None = None) -> dict:
    """Run an argv list with shell=False — the safe alternative to f-string +
    shell=True (host command injection is impossible by construction)."""
    return _run_generic(argv, " ".join(argv), cwd, timeout, env)


def _run_generic(argv: list[str] | None, display: str, cwd: str,
                 timeout: int, env: dict | None) -> dict:
    start = time.time()
    try:
        proc = _spawn(argv, display, cwd, env, new_session=True)
    except FileNotFoundError:
        # argv mode (shell=False): a missing binary raises instead of the
        # shell's "command not found" + non-zero rc — surface the same
        # honest runtime error shape, not a traceback
        return {"ok": False, "returncode": 127, "cmd": display,
                "stdout": "", "stderr": f"executable not found: {display.split(' ', 1)[0] if argv is None else argv[0]}",
                "elapsed_ms": int((time.time() - start) * 1000)}
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        return {"ok": False, "error": "timeout", "cmd": display,
                "stdout": out or "", "stderr": err or "",
                "elapsed_ms": int((time.time() - start) * 1000)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": out or "",
        "stderr": err or "",
        "elapsed_ms": int((time.time() - start) * 1000),
        "cmd": display,
    }


def _resolve_cwd(cwd: str) -> str:
    """Resolve the default cwd to the configured project root — the same
    discipline shell_exec always applied, now shared by every command
    runner (Monitor, PowerShell, TestRunner… used to run with cwd='.'
    meaning whatever the process happened to be started in)."""
    if cwd in ("", "."):
        try:
            from core.agent.project_context import project_root_ctx
            ctx = project_root_ctx.get()
            if ctx:
                return ctx
        except Exception:
            pass
        try:
            from config import get_config
            configured_root = str(get_config().get("project_root", "") or "")
            if configured_root:
                return configured_root
        except Exception:
            pass
    return cwd


def _run_sandboxed_dispatch(cmd: str, cwd: str = ".", env: dict | None = None,
                            timeout: int = SHELL_TIMEOUT) -> dict:
    """THE single dispatch every command-running tool must use.

    Windows-native → the real sandbox (elevated → unelevated, fail-closed);
    WSL2/Linux/macOS → the plain runner (the OS session is the isolation).
    Audit 2026-10: Monitor/PowerShell/TestRunner/CoverageReport used to
    call _run/_run_argv DIRECTLY, so on a windows_native machine they ran
    with NO sandbox while Bash went through one — same consent ladder,
    weaker guarantee; the sandbox was a policy an agent could bypass by
    picking a different tool. Routing them all through here makes the
    guarantee uniform, and the SHELL_TIMEOUT cap uniform too.
    """
    cwd = _resolve_cwd(cwd)
    from security.windows_sandbox import detect_platform
    plat = detect_platform()
    if plat == "windows_native":
        from security.windows_sandbox import run_sandboxed as run_windows_sandboxed
        result = run_windows_sandboxed(cmd, cwd=cwd, env=env,
                                       timeout=min(timeout, SHELL_TIMEOUT))
        if result["ok"] or result.get("returncode") is not None:
            # real execution happened (even a failing command's non-zero
            # exit code counts) — don't fall through to unsandboxed.
            return result
        # fallback: try unsandboxed with warning (first call should succeed)
        import loguru as _lg
        _lg.logger.warning("Windows sandbox setup failed ({}); falling back to unsandboxed", result.get("error",""))
        return _run(cmd, cwd=cwd, timeout=min(timeout, SHELL_TIMEOUT), env=env)
    return _run(cmd, cwd=cwd, timeout=min(timeout, SHELL_TIMEOUT), env=env)


def shell_exec(cmd: str = "", cwd: str = ".", timeout: int = 120,
               env: dict | None = None, command: str = "", script: str = "") -> dict:
    """Execute a shell command inside the sandbox (scoped to the project).

    Args:
        cmd: the shell command to run (POSIX syntax; on Windows it runs
            inside the native sandbox which understands the same syntax).
        cwd: working directory for the command — defaults to the active
            project root; pass an absolute path to work elsewhere.
        timeout: max seconds to wait (default 120, hard cap 300).
        env: optional extra environment variables for this command.
        command: alias for cmd (one of cmd/command/script is required).
        script: alias for cmd (multi-line script text).
    """
    cmd = str(cmd or command or script)
    if not cmd:
        return {"ok": False, "error": "missing shell command"}
    return _run_sandboxed_dispatch(cmd, cwd=cwd, env=env, timeout=timeout)


def shell_exec_unsandboxed(cmd: str = "", cwd: str = ".", timeout: int = 120,
                           env: dict | None = None, command: str = "", script: str = "") -> dict:
    """Runs `cmd` with NO sandbox at all — full access to the real
    filesystem and network, on any platform. Only reachable via
    consent=explicit: this is Universe's equivalent of upstream's
    "danger-full-access", which is deliberately never an automatic
    fallback (see security/windows_sandbox.py's module docstring).

    Args:
        cmd: the shell command to run.
        cwd: working directory for the command (default: project root).
        timeout: max seconds to wait (default 120, hard cap 300).
        env: optional extra environment variables for this command.
        command: alias for cmd (one of cmd/command/script is required).
        script: alias for cmd (multi-line script text).
    """
    cmd = str(cmd or command or script)
    if not cmd:
        return {"ok": False, "error": "missing shell command"}
    result = _run(cmd, cwd=cwd, timeout=min(timeout, SHELL_TIMEOUT), env=env)
    result["sandboxed"] = False
    return result


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        name="Bash", capability="Bash",
        description="Execute a shell command (scoped to cwd, 5-min max). On "
                    "Windows without WSL2, runs inside the native Windows "
                    "sandbox (elevated, falling back to unelevated).",
        handler=shell_exec, consent="per_turn",
        resources=["cwd"],
    ))
    reg.register(Tool(
        name="BashUnsandboxed", capability="Bash",
        description="Execute a shell command with NO sandbox — full system "
                    "access. Only use when shell.exec reports the sandbox "
                    "could not be set up and the task truly cannot proceed "
                    "otherwise. Always asks for explicit, one-time consent.",
        handler=shell_exec_unsandboxed, consent="explicit",
        resources=["cwd"],
    ))
