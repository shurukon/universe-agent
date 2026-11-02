"""connectors/builtin/computer_use.py — the machine.control tools (Phase D).

ZCode computer-use parity over the universe_cua_helper broker: a separate
process speaking NDJSON (see universe_cua_helper.py). Every tool here is
consent=explicit — the user approves the FIRST machine.control call of a
goal through the consent banner, and the TransparencyBar ("Universe is
controlling your computer", Phase B) rides the computer.* tool events.

Safety model (report-03):
  * frame binding lives in the broker: coordinate actions must reference
    a live screenshot frame (15s TTL) and fall inside it — a stale frame
    is rejected, never replayed onto whatever the user opened since
  * a11y-first: text entry goes through UIA ValuePattern (set_value),
    never raw keystrokes (this machine's injected-input filter eats
    synthetic spaces anyway)
  * the helper is spawned lazily and dies with the bridge; it never
    decides policy — the gate + consent layer owns every decision
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from typing import Any

from core.agent.base_agent import Tool

_HELPER_TIMEOUT_S = 60.0  # VL needs longer for screenshot + vision  # screenshots/UIA walks can take a few seconds


class _Broker:
    """One lazy universe_cua_helper child process, NDJSON over stdio."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._seq = 0
        self._lock = threading.Lock()

    def _ensure(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            # cwd = repo root so `python -m universe_cua_helper` resolves
            import os
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self._proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "universe_cua_helper"],
                cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace")
        return self._proc

    def call(self, op: str, **args: Any) -> dict[str, Any]:
        with self._lock:
            proc = self._ensure()
            self._seq += 1
            rid = self._seq
            req = json.dumps({"id": rid, "op": op, **args})
            try:
                proc.stdin.write(req + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                # dead broker: one restart, then a real error surfaces
                self.kill()
                proc = self._ensure()
                proc.stdin.write(req + "\n")
                proc.stdin.flush()
            # read lines until our id answers (a stale line never matches)
            import time as _time
            deadline = _time.time() + _HELPER_TIMEOUT_S
            while _time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    self.kill()
                    return {"ok": False,
                            "error": "computer-use broker died mid-request"}
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue  # broker noise — keep reading
                if resp.get("id") == rid:
                    return resp
            return {"ok": False,
                    "error": "computer-use broker timed out — retry"}

    def kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None


_broker = _Broker()


def _call(op: str, **args: Any) -> dict[str, Any]:
    """Sync broker round-trip. Handlers run off the orchestrator's loop via
    asyncio.to_thread (registered below), so blocking here never blocks."""
    try:
        return _broker.call(op, **args)
    except Exception as exc:  # spawn failure, import error, … — honest error
        return {"ok": False, "error": f"broker call failed: {exc}"[:200]}


# ---- handlers (thin: validate args, round-trip the broker) -------------------

def _request_access(**_: Any) -> dict[str, Any]:
    """Report what machine control is available. The GRANT itself is the
    explicit consent dialog the gate raises on the first machine.control
    call of the goal — this op is the honest capability probe."""
    r = _call("capabilities")
    if r.get("ok"):
        r["consent_model"] = ("every machine.control call this goal is "
                              "approved through the consent banner")
    return r


def _list_apps(**_: Any) -> dict[str, Any]:
    return _call("windows")


def _list_windows(**_: Any) -> dict[str, Any]:
    return _call("windows")


def _get_app_state(hwnd: int = 0, title: str = "", **kw: Any) -> dict[str, Any]:
    if not hwnd and not title:
        return {"ok": False,
                "error": "get_app_state needs a window (hwnd or title)"}
    return _call("app_state", hwnd=hwnd, title=title, **kw)


def _screenshot(hwnd: int = 0, **_: Any) -> dict[str, Any]:
    return _call("screenshot", hwnd=hwnd) if hwnd else _call("screenshot")


def _zoom(frame_id: str = "", x: int = 0, y: int = 0, w: int = 0,
          h: int = 0, **_: Any) -> dict[str, Any]:
    if not frame_id:
        return {"ok": False, "error": "zoom needs the source frame_id"}
    return _call("zoom", frame_id=frame_id, x=x, y=y, w=w, h=h)


def _mouse(kind: str):
    def run(frame_id: str = "", x: int = 0, y: int = 0, **_: Any) -> dict[str, Any]:
        if not frame_id:
            return {"ok": False,
                    "error": f"{kind} needs a live frame_id from computer.screenshot"}
        return _call("mouse", frame_id=frame_id, x=x, y=y, kind=kind)
    return run


def _scroll(frame_id: str = "", x: int = 0, y: int = 0, amount: int = -3,
            **_: Any) -> dict[str, Any]:
    if not frame_id:
        return {"ok": False, "error": "scroll needs a live frame_id"}
    return _call("scroll", frame_id=frame_id, x=x, y=y, amount=amount)


def _swipe(frame_id: str = "", x1: int = 0, y1: int = 0, x2: int = 0,
           y2: int = 0, **_: Any) -> dict[str, Any]:
    if not frame_id:
        return {"ok": False, "error": "swipe needs a live frame_id"}
    return _call("drag", frame_id=frame_id, x1=x1, y1=y1, x2=x2, y2=y2)


def _set_value(hwnd: int = 0, title: str = "", name: str = "",
               value: str = "", **_: Any) -> dict[str, Any]:
    if not (hwnd or title):
        return {"ok": False, "error": "set_value needs a window (hwnd or title)"}
    if not value:
        return {"ok": False, "error": "set_value needs the value to type"}
    return _call("set_value", hwnd=hwnd, title=title, name=name, value=value)


def _open_application(target: str = "", **_: Any) -> dict[str, Any]:
    return _call("open_app", target=target)


def _switch_display(index: int = 0, **_: Any) -> dict[str, Any]:
    return _call("switch_display", index=index)


def _to_thread_handler(handler):
    """The gate invokes handlers synchronously inside asyncio contexts; the
    broker blocks on pipes, so run it on a worker thread."""
    import asyncio
    import functools
    import inspect
    if inspect.iscoroutinefunction(handler):
        return handler

    # functools.preserves the ORIGINAL handler's signature (__wrapped__)
    # so the model-facing JSON schema keeps the real	parameters (schema
    # round: without it the wrapper's bare **kwargs wiped every argument
    # the computer.use tools accept and the model could not supply
    # frame_id/hwnd/x/y at all).
    @functools.wraps(handler)
    async def wrapped(**kwargs: Any) -> dict[str, Any]:
        return await asyncio.to_thread(handler, **kwargs)
    return wrapped


def register(reg) -> None:
    cap = "machine.control"

    def t(name: str, desc: str, handler) -> Tool:
        return Tool(name=name, capability=cap, description=desc,
                    handler=_to_thread_handler(handler), consent="explicit")

    reg.register(t(
        "RequestAccess",
        "Probe machine-control capabilities (displays, input model, frame "
        "TTL). The actual grant is the explicit consent dialog on the "
        "first machine.control call of the goal.",
        _request_access))
    reg.register(t(
        "ListApps",
        "List every visible top-level window: title, process, rect.",
        _list_apps))
    reg.register(t(
        "ListWindows",
        "Alias of computer.list_apps — visible windows with rects.",
        _list_windows))
    reg.register(t(
        "GetAppState",
        "Read a window's accessibility tree (named controls + rects) — "
        "the a11y-first read; prefer it over pixels when the app exposes "
        "names. Args: hwnd or title.",
        _get_app_state))
    reg.register(t(
        "Screenshot",
        "Capture the screen (or one window by hwnd) as a PNG frame. "
        "Returns frame_id + path + size; the frame stays valid until its "
        "TTL expires (desktop.computer_frame_ttl_s, default 45s) and "
        "every coordinate action must reference a frame that is still "
        "live.",
        _screenshot))
    reg.register(t(
        "Zoom",
        "Crop + upscale a region of a live frame for closer reading. "
        "Args: frame_id, x, y, w, h.",
        _zoom))
    for tool_name, op_kind, label in (
        ("LeftClick", "click", "left click"),
        ("RightClick", "right", "right click"),
        ("DoubleClick", "double", "double click"),
        ("LongPress", "long", "long press"),
    ):
        reg.register(t(
            tool_name,
            f"Mouse {label} at (x, y) inside a LIVE "
            "frame — stale frames are rejected. Args: frame_id, x, y.",
            _mouse(op_kind)))
    reg.register(t(
        "Swipe",
        "Drag from (x1,y1) to (x2,y2) inside a live frame.",
        _swipe))
    reg.register(t(
        "Scroll",
        "Mouse-wheel scroll at frame coordinates. amount: negative = down, "
        "positive = up, ~3 per notch.",
        _scroll))
    reg.register(t(
        "SetValue",
        "Set a control's text through UI Automation (no keystrokes). "
        "Args: hwnd or title, name (the control's accessible name), value.",
        _set_value))
    reg.register(t(
        "OpenApplication",
        "Start an application by name (notepad, explorer, calculator, cmd) "
        "or an absolute path.",
        _open_application))
    reg.register(t(
        "SwitchDisplay",
        "Point the cursor at another display (index from "
        "computer.request_access). Mode switching stays with the user's "
        "display settings.",
        _switch_display))
