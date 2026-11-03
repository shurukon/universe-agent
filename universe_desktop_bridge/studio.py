"""universe_desktop_bridge/studio.py — the Studio service manager.

Universe Studio embeds three REAL tools as first-class modules:
  ave        — the user's own AI video editor (github.com/shurukon/AVE)
               Next.js app, driven over its MCP server (41 tools, :19789)
  opendesign — open-design (github.com/nexu-io/open-design) — websites &
               presentations, daemon API on :7456
  blender    — Blender via blender-mcp (addon socket :9876); no managed
               process — the user runs Blender, we probe the socket.

The manager clones the service repo into config.home/studio-services/<id>
on first start, installs it, launches it detached (logs to services/<id>.log)
and reports live health by probing the port. Nothing is simulated: a status
is "running" only when the port actually answers.
"""
from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess  # noqa: S404 — local, user-initiated service launch
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from config import get_config

SERVICES: dict[str, dict[str, Any]] = {
    "ave": {
        "name": "AVE — Video Editor",
        "description": "Timeline editing, montage and effects — driven by "
                       "Universe through AVE's MCP server (41 tools).",
        "repo": "https://github.com/shurukon/AVE",
        # 3011, NOT AVE's default 3000 — the Universe UI itself owns 3000.
        # AVE ships two processes: the editor (Next.js) and its MCP server.
        "port": 3011,
        "url": "http://127.0.0.1:3011",
        "install": ["npm", "install", "--no-audit", "--no-fund",
                    "--cache", "/tmp/universe-npm-cache"],
        "run": [["node", "mcp-server/server.js"],
                ["npx", "next", "dev", "-p", "3011"]],
    },
    "opendesign": {
        "name": "Open Design",
        "description": "Websites & presentations engine — artifacts exported "
                       "to HTML, PDF and JSX.",
        "repo": "https://github.com/nexu-io/open-design",
        "port": 7456,
        "url": "http://127.0.0.1:7456",
        "install": ["pnpm", "install"],
        "run": [["pnpm", "--filter", "@open-design/daemon", "dev"]],
    },
    "blender": {
        "name": "Blender 3D",
        "description": "3D modelling & rendering through blender-mcp. Open "
                       "Blender, enable the MCP addon, choose Preview or "
                       "Background.",
        "repo": "https://github.com/ahujasid/blender-mcp",
        "port": 9876,
        "url": "tcp://127.0.0.1:9876",
        "install": None,
        "run": None,  # external: the Blender addon owns the socket
    },
}

_procs: dict[str, list[subprocess.Popen[Any]]] = {}
_starting: dict[str, float] = {}
_last_probe: dict[str, tuple[float, str]] = {}
_PROBE_TTL = 6.0


def _resolve_cmd(argv: list[str]) -> list[str]:
    """Map a launch command onto what this machine actually has. pnpm is
    optional — npx can fetch it on demand; npm/node are assumed present."""
    if argv and argv[0] == "pnpm" and shutil.which("pnpm") is None:
        return ["npx", "--yes", "pnpm", *argv[1:]]
    return argv


def _services_dir() -> Path:
    d = get_config().home / "studio-services"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_file(sid: str) -> Path:
    return _services_dir() / f"{sid}.log"


def _probe_tcp(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


async def _probe_http(url: str, timeout: float = 2.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            r = await c.get(url)
            return r.status_code < 500
    except Exception:
        return False


async def _status(sid: str) -> str:
    cfg = SERVICES[sid]
    port = int(cfg["port"])
    if sid == "blender":
        ok = await asyncio.to_thread(_probe_tcp, "127.0.0.1", port)
        return "running" if ok else "stopped"
    host = "127.0.0.1"
    ok = await _probe_http(f"http://{host}:{port}/")
    if ok:
        return "running"
    if sid in _starting and time.time() - _starting[sid] < 600:
        return "starting"
    procs = _procs.get(sid) or []
    if procs and any(p.poll() is None for p in procs):
        return "starting"
    return "stopped"


async def list_services() -> list[dict[str, Any]]:
    out = []
    for sid, cfg in SERVICES.items():
        status = await _status(sid)
        _last_probe[sid] = (time.time(), status)
        detail = ""
        svc_dir = _services_dir() / sid
        if sid != "blender" and not svc_dir.exists():
            detail = "not cloned yet — first start clones the repo"
        out.append({"id": sid, "name": cfg["name"],
                    "description": cfg["description"], "url": cfg["url"],
                    "port": cfg["port"], "status": status, "detail": detail})
    return out


async def service_status_cached(sid: str) -> str:
    ts, val = _last_probe.get(sid, (0.0, "stopped"))
    if time.time() - ts > _PROBE_TTL:
        val = await _status(sid)
        _last_probe[sid] = (time.time(), val)
    return val


async def start_service(sid: str) -> dict[str, Any]:
    cfg = SERVICES[sid]
    if sid == "blender":
        if await _probe_tcp("127.0.0.1", int(cfg["port"])):
            return {"ok": True, "status": "running",
                    "detail": "Blender MCP addon is reachable."}
        return {"ok": False, "status": "stopped",
                "detail": "Blender is not reachable on port 9876 — open "
                          "Blender and enable the blender-mcp addon."}
    svc_dir = _services_dir() / sid
    log_fh = open(_log_file(sid), "ab", buffering=0)  # noqa: SIM115 — lives for process lifetime

    def _run_blocking() -> int:
        if not svc_dir.exists():
            log_fh.write(b"[universe] cloning repository...\n")
            subprocess.run(["git", "clone", "--depth", "1", cfg["repo"],
                            str(svc_dir)], check=True,
                           stdout=log_fh, stderr=log_fh, timeout=600)
        if cfg.get("install"):
            log_fh.write(f"[universe] installing ({sid})...\n".encode())
            subprocess.run(_resolve_cmd(list(cfg["install"])),
                           cwd=str(svc_dir), check=True,
                           stdout=log_fh, stderr=log_fh, timeout=1800)
        log_fh.write(f"[universe] launching ({sid})...\n".encode())
        # `run` is a list of launch commands (a service may own several
        # processes, e.g. AVE = MCP server + editor). Spawn each detached.
        procs: list[subprocess.Popen[Any]] = []
        for argv in cfg["run"]:
            procs.append(subprocess.Popen(  # noqa: S603 — fixed argv from our own catalog
                _resolve_cmd(list(argv)), cwd=str(svc_dir),
                stdout=log_fh, stderr=log_fh, start_new_session=True))
        _procs[sid] = procs
        return 0

    _starting[sid] = time.time()
    try:
        await asyncio.to_thread(_run_blocking)
    except Exception as exc:  # noqa: BLE001
        _starting.pop(sid, None)
        logger.warning(f"studio start {sid} failed: {exc}")
        return {"ok": False, "status": "stopped",
                "detail": f"start failed: {exc} — see {_log_file(sid)}"}
    return {"ok": True, "status": "starting",
            "detail": f"launched — logs: {_log_file(sid)}"}


async def stop_service(sid: str) -> dict[str, Any]:
    if sid == "blender":
        return {"ok": True, "status": await _status(sid),
                "detail": "Blender is external — close it from Blender itself."}
    procs = _procs.pop(sid, None) or []
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
    _starting.pop(sid, None)
    return {"ok": True, "status": "stopped", "detail": ""}


def reset_for_tests() -> None:
    _procs.clear()
    _starting.clear()
    _last_probe.clear()
