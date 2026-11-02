"""security/sandbox.py — Docker sandbox for quarantine-tier shell.exec (§10).

shell.exec for Quarantine-tier (community/unsigned) skills runs in a Docker
container with --network=none and a read-only filesystem (optional scratch).
shell.exec for trusted, signed L4 skills runs natively, through the gate.
"""
from __future__ import annotations

import subprocess
from typing import Any


def is_docker_available() -> bool:
    try:
        r = subprocess.run("docker info", shell=True, capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def run_sandboxed(command: str, *, image: str = "python:3.12-slim",
                  network: str = "none", read_only: bool = True,
                  scratch_mb: int = 64, timeout: int = 60) -> dict[str, Any]:
    """Run a command in a Docker sandbox. Default: no network, read-only FS,
    with a tmpfs scratch space. Returns stdout/stderr/returncode."""
    flags = ["--rm", f"--network={network}"]
    if read_only:
        flags.append("--read-only")
    if scratch_mb > 0:
        flags.append(f"--tmpfs /scratch:rw,size={scratch_mb}m")
    flags.append("--memory=256m")
    flags.append("--cpus=0.5")
    cmd = f"docker run {' '.join(flags)} {image} sh -c {repr(command)}"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "returncode": r.returncode,
                "stdout": r.stdout, "stderr": r.stderr, "sandboxed": True}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "sandbox timeout", "sandboxed": True}
    except Exception as e:
        return {"ok": False, "error": str(e), "sandboxed": True}


def verify_no_network(image: str = "python:3.12-slim") -> bool:
    """Test that the sandbox genuinely has no network: try to reach a host and
    confirm it fails. Used by the security test suite (§STEP 5)."""
    result = run_sandboxed("python -c \"import urllib.request; urllib.request.urlopen('https://example.com', timeout=3)\"",
                           image=image, network="none", read_only=True, timeout=15)
    # success means network LEAKED (bad); failure means sandbox is working
    return not result.get("ok")
