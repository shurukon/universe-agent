"""universe_cua_helper.py — the computer-use broker (Phase D, report-03).

A SEPARATE process from the bridge, speaking NDJSON over stdio: one JSON
request per line in, one JSON response per line out ({"id"} matched).
The bridge-side tools (connectors/builtin/computer_use.py) spawn this
helper lazily and speak the protocol; the helper does the actual Windows
work:

  windows / app_state / set_value   — UI Automation (pywinauto, comtypes)
  screenshot / zoom                 — PIL ImageGrab, frames on disk
  mouse / scroll / drag             — SendInput at frame-validated points
  activate / open_app / displays    — Win32 + shell

Design rules (report-03, verified live in the reference product):
  * frame binding — every screenshot gets a frame_id with a TTL; every
    coordinate action must reference a LIVE frame and fall inside it, so
    a stale screenshot can never drive a click onto whatever the user
    opened since (the reference product rejects exactly this way)
  * a11y-first — set_value goes through UIA's ValuePattern, never raw
    keystrokes; on this machine raw synthetic SPACE is eaten by an
    injected-input filter, so UIA is the reliable path anyway
  * the helper NEVER decides policy: consent lives in the tool layer
    (consent=explicit → the user approves through the consent banner)

Run standalone for a smoke: `python -m universe_cua_helper` then type
  {"id": 1, "op": "capabilities"}
on stdin.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

def _load_frame_ttl() -> float:
    """Frame TTL from desktop.computer_frame_ttl_s (default 15). A short
    hardwired 15 s made every screenshot, click pair of a real agent turn
    expire before the model's second round landed, so coordinate actions
    were rejected purely on wall-clock time. The override was added in the
    tools-matrix round — the safety semantics (an expired frame NEVER
    drives a click) stay exactly the same, the bound is just tunable."""
    raw = 15.0
    try:
        cfgp = os.path.join(os.path.expanduser("~"), ".universe", "config.json")
        import json as _json
        raw = float(_json.load(open(cfgp, encoding="utf-8"))
                     .get("desktop", {}).get("computer_frame_ttl_s", 15.0))
    except Exception:
        raw = 15.0
    return raw if 1.0 <= raw <= 600.0 else 15.0


FRAME_TTL_S = _load_frame_ttl()  # default 15; override desktop.computer_frame_ttl_s

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

_frames: dict[str, dict] = {}
_frame_dir: str | None = None


def _frame_root() -> str:
    global _frame_dir
    if _frame_dir is None or not os.path.isdir(_frame_dir):
        _frame_dir = os.path.join(
            tempfile.gettempdir(), "universe-cua-frames")
        os.makedirs(_frame_dir, exist_ok=True)
    return _frame_dir


# ---- windowing ------------------------------------------------------------

def _win_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _win_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _pid_of(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _exe_of(pid: int) -> str:
    try:
        import psutil
        return os.path.basename(psutil.Process(pid).exe() or "")
    except Exception:
        return ""


def op_windows(**_):
    """Every visible top-level window with a title."""
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _win_title(hwnd)
        if not title:
            return True
        pid = _pid_of(hwnd)
        left, t, r, b = _win_rect(hwnd)
        out.append({"hwnd": hwnd, "pid": pid, "exe": _exe_of(pid),
                    "title": title[:160],
                    "rect": {"left": left, "top": t, "right": r, "bottom": b}})
        return True

    user32.EnumWindows(cb, 0)
    return {"ok": True, "windows": out}


def op_activate(hwnd: int = 0, **_) -> dict:
    if not hwnd or not user32.IsWindow(hwnd):
        return {"ok": False, "error": f"no such window: {hwnd}"}
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    this_tid = kernel32.GetCurrentThreadId()
    if fg_tid != this_tid:
        user32.AttachThreadInput(this_tid, fg_tid, True)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # the ALT nudge: documented focus-stealing workaround
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 0x0002, 0)
    if fg_tid != this_tid:
        user32.AttachThreadInput(this_tid, fg_tid, False)
    time.sleep(0.15)
    return {"ok": user32.GetForegroundWindow() == hwnd,
            "hwnd": hwnd, "foreground": user32.GetForegroundWindow()}


# ---- frames (screenshot / zoom) -------------------------------------------

def _capture(rect: tuple[int, int, int, int] | None) -> dict:
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=rect) if rect else ImageGrab.grab()
    fid = f"frame-{uuid.uuid4().hex[:12]}"
    path = os.path.join(_frame_root(), f"{fid}.png")
    img.save(path)
    w, h = img.size
    frame = {"id": fid, "path": path, "w": w, "h": h,
             "rect": rect, "ts": time.time()}
    _frames[fid] = frame
    # opportunistic cleanup of long-dead frames
    now = time.time()
    for k in [k for k, f in _frames.items() if now - f["ts"] > 300]:
        try:
            os.remove(_frames[k]["path"])
        except OSError:
            pass
        _frames.pop(k, None)
    return {"ok": True, "frame_id": fid, "path": path, "width": w, "height": h,
            "ttl_s": FRAME_TTL_S,
            "expires_at": round(frame["ts"] + FRAME_TTL_S, 3)}


def op_screenshot(hwnd: int = 0, **_) -> dict:
    if hwnd:
        if not user32.IsWindow(hwnd):
            return {"ok": False, "error": f"no such window: {hwnd}"}
        return _capture(_win_rect(hwnd))
    return _capture(None)


def op_zoom(frame_id: str = "", x: int = 0, y: int = 0, w: int = 0,
            h: int = 0, **_) -> dict:
    src = _live_frame(frame_id)
    if src is None:
        return {"ok": False,
                "error": "frame expired or unknown — take a fresh screenshot"}
    if x < 0 or y < 0 or x + w > src["w"] or y + h > src["h"] or w <= 0 or h <= 0:
        return {"ok": False, "error": "zoom rect falls outside the frame"}
    from PIL import Image
    img = Image.open(src["path"]).crop((x, y, x + w, y + h))
    # upscale small crops so text stays legible (the reference zoom)
    if img.width < 512 and img.height < 512:
        scale = min(4, max(2, 512 // max(1, min(img.width, img.height))))
        img = img.resize((img.width * scale, img.height * scale))
    fid = f"frame-{uuid.uuid4().hex[:12]}"
    path = os.path.join(_frame_root(), f"{fid}.png")
    img.save(path)
    _frames[fid] = {"id": fid, "path": path, "w": img.width, "h": img.height,
                    "rect": None, "ts": time.time()}
    return {"ok": True, "frame_id": fid, "path": path,
            "width": img.width, "height": img.height, "ttl_s": FRAME_TTL_S,
            "expires_at": round(time.time() + FRAME_TTL_S, 3)}


def _live_frame(frame_id: str) -> dict | None:
    f = _frames.get(str(frame_id or ""))
    if f is None:
        return None
    if time.time() - f["ts"] > FRAME_TTL_S:
        return None
    return f


# ---- pointer actions (frame-bound) -----------------------------------------

def _bound(frame_id: str, x: int, y: int) -> dict | None:
    f = _live_frame(frame_id)
    if f is None:
        return {"ok": False,
                "error": "frame expired — retake the screenshot, then act"}
    if not (0 <= x < f["w"] and 0 <= y < f["h"]):
        return {"ok": False,
                "error": f"({x},{y}) falls outside the frame "
                         f"({f['w']}x{f['h']}) — the screen changed; retake"}
    return None


INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800


class _MI(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)]


class _KI(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p)]


class _IN(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MI), ("ki", _KI)]
    _anonymous_ = ["u"]
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _mouse(flags: int, dx: int = 0, dy: int = 0) -> None:
    i = _IN(type=INPUT_MOUSE)
    i.u.mi = _MI(dx, dy, flags, 0, None)
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(_IN))


def _to_screen(frame_id: str, x: int, y: int) -> tuple[int, int] | None:
    f = _frames.get(str(frame_id or ""))
    if f is None or f.get("rect") is None:
        return None  # full-screen frame: coordinates map 1:1
    left, t, _, _ = f["rect"]
    return left + x, t + y


def op_mouse(frame_id: str = "", x: int = 0, y: int = 0,
             kind: str = "click", hold_ms: int = 600, **_) -> dict:
    bad = _bound(frame_id, x, y)
    if bad:
        return bad
    sx, sy = _to_screen(frame_id, x, y) or (x, y)
    user32.SetCursorPos(sx, sy)
    time.sleep(0.05)
    if kind == "click":
        _mouse(MOUSEEVENTF_LEFTDOWN)
        time.sleep(0.02)
        _mouse(MOUSEEVENTF_LEFTUP)
    elif kind == "double":
        for _ in range(2):
            _mouse(MOUSEEVENTF_LEFTDOWN)
            time.sleep(0.02)
            _mouse(MOUSEEVENTF_LEFTUP)
            time.sleep(0.05)
    elif kind == "right":
        _mouse(MOUSEEVENTF_RIGHTDOWN)
        time.sleep(0.02)
        _mouse(MOUSEEVENTF_RIGHTUP)
    elif kind == "long":
        _mouse(MOUSEEVENTF_LEFTDOWN)
        time.sleep(max(0.05, hold_ms) / 1000.0)
        _mouse(MOUSEEVENTF_LEFTUP)
    else:
        return {"ok": False, "error": f"unknown mouse kind {kind!r}"}
    return {"ok": True, "kind": kind, "x": x, "y": y, "frame_id": frame_id}


def op_scroll(frame_id: str = "", x: int = 0, y: int = 0,
              amount: int = -3, **_) -> dict:
    bad = _bound(frame_id, x, y)
    if bad:
        return bad
    sx, sy = _to_screen(frame_id, x, y) or (x, y)
    user32.SetCursorPos(sx, sy)
    time.sleep(0.05)
    _mouse(MOUSEEVENTF_WHEEL, dy=int(amount) * 120)
    return {"ok": True, "x": x, "y": y, "amount": amount}


def op_drag(frame_id: str = "", x1: int = 0, y1: int = 0, x2: int = 0,
            y2: int = 0, hold_ms: int = 120, **_) -> dict:
    for bad in (_bound(frame_id, x1, y1), _bound(frame_id, x2, y2)):
        if bad:
            return bad
    s1 = _to_screen(frame_id, x1, y1) or (x1, y1)
    s2 = _to_screen(frame_id, x2, y2) or (x2, y2)
    user32.SetCursorPos(*s1)
    time.sleep(0.05)
    _mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(max(0.03, hold_ms) / 1000.0)
    # move in steps so apps that track motion see a real drag
    steps = 12
    for i in range(1, steps + 1):
        user32.SetCursorPos(
            s1[0] + (s2[0] - s1[0]) * i // steps,
            s1[1] + (s2[1] - s1[1]) * i // steps)
        time.sleep(0.01)
    _mouse(MOUSEEVENTF_LEFTUP)
    return {"ok": True, "from": [x1, y1], "to": [x2, y2]}


# ---- UIA (app_state / set_value) -------------------------------------------

def _uia_window(title: str = "", hwnd: int = 0):
    from pywinauto import Desktop
    uia = Desktop(backend="uia")
    if hwnd:
        return uia.window(handle=hwnd)
    if not title:
        raise ValueError("app_state needs a window title or hwnd")
    return uia.window(title=title)


def op_app_state(title: str = "", hwnd: int = 0, max_depth: int = 6,
                 max_nodes: int = 120, **_) -> dict:
    """The named a11y tree of one window — the a11y-first read the agent
    reasons over instead of raw pixels."""
    try:
        win = _uia_window(title, hwnd)
    except Exception as exc:
        return {"ok": False, "error": f"window not found: {exc}"[:200]}
    nodes: list[dict] = []
    try:
        def walk(ctrl, depth):
            if depth > max_depth or len(nodes) >= max_nodes:
                return
            try:
                name = (ctrl.element_info.name or "")[:120]
                ctype = ctrl.element_info.control_type or ""
                if name:
                    r = ctrl.rectangle()
                    nodes.append({
                        "depth": depth, "type": ctype, "name": name,
                        "rect": [r.left, r.top, r.right, r.bottom]})
            except Exception:
                return
            for ch in ctrl.children():
                walk(ch, depth + 1)
        walk(win, 0)
    except Exception as exc:
        return {"ok": False, "error": f"a11y walk failed: {exc}"[:200]}
    return {"ok": True, "controls": nodes}


def op_set_value(title: str = "", hwnd: int = 0, name: str = "",
                 value: str = "", **_) -> dict:
    """Set a control's text through UIA's ValuePattern — no keystrokes, so
    no keyboard-filter quirks and no focus stealing."""
    if not value:
        return {"ok": False, "error": "value is required"}
    try:
        win = _uia_window(title, hwnd)
        el = win.child_window(title=name) if name else win
        el.wait("visible ready", timeout=4)
    except Exception as exc:
        return {"ok": False, "error": f"control not found: {exc}"[:200]}
    try:
        el.set_edit_text(str(value))
    except Exception as exc:
        return {"ok": False,
                "error": f"set_value failed (not a text control?): {exc}"[:200]}
    return {"ok": True, "control": name, "value": str(value)[:200]}


# ---- launcher / displays ----------------------------------------------------

KNOWN_APPS = {
    "notepad": "notepad.exe",
    "explorer": "explorer.exe",
    "calculator": "calc.exe",
    "cmd": "cmd.exe",
}


def op_open_app(target: str = "", **_) -> dict:
    target = str(target or "").strip()
    if not target:
        return {"ok": False, "error": "target is required"}
    exe = KNOWN_APPS.get(target.lower(), target)
    try:
        subprocess.Popen([exe], shell=False)
    except Exception as exc:
        return {"ok": False, "error": f"could not start {target!r}: {exc}"[:200]}
    return {"ok": True, "started": target}


def op_displays(**_) -> dict:
    class DM(ctypes.Structure):
        _fields_ = [("dmDeviceName", wt.WCHAR * 32),
                    ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
                    ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD),
                    ("dmFields", wt.DWORD),
                    ("dmPositionX", wt.LONG), ("dmPositionY", wt.LONG)]
    dm = DM()
    dm.dmSize = ctypes.sizeof(DM)
    out = []
    i = 0
    while user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
        out.append({"index": i, "x": dm.dmPositionX, "y": dm.dmPositionY})
        i += 1
        if i > 8:
            break
    return {"ok": True, "displays": out}


def op_switch_display(index: int = 0, **_) -> dict:
    d = op_displays()
    for it in d.get("displays", []):
        if it["index"] == index:
            # move the cursor to the target display's origin — the visible
            # signal of a display switch; actual mode switching needs the
            # user's display settings (documented limit, same as reference)
            user32.SetCursorPos(it["x"] + 100, it["y"] + 100)
            return {"ok": True, "display": it}
    return {"ok": False, "error": f"no display #{index}"}


def op_capabilities(**_) -> dict:
    d = op_displays()
    return {"ok": True,
            "helper": "universe_cua_helper",
            "os": "windows",
            "displays": len(d.get("displays", [])),
            "frame_ttl_s": FRAME_TTL_S,
            "input_model": {"raw_mouse": True, "text_via": "uia_value_pattern",
                            "note": "synthetic keyboard quirks are bypassed "
                                    "by setting values through UIA"}}


# ---- the NDJSON loop ----------------------------------------------------------

_OPS = {
    "capabilities": op_capabilities,
    "windows": op_windows,
    "activate": op_activate,
    "screenshot": op_screenshot,
    "zoom": op_zoom,
    "mouse": op_mouse,
    "scroll": op_scroll,
    "drag": op_drag,
    "app_state": op_app_state,
    "set_value": op_set_value,
    "open_app": op_open_app,
    "displays": op_displays,
    "switch_display": op_switch_display,
}


def main() -> int:
    # binary-safe, line-framed JSON on stdio (NDJSON)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"ok": False, "id": None,
                                         "error": "invalid json line"}) + "\n")
            sys.stdout.flush()
            continue
        rid = req.get("id")
        op = str(req.get("op") or "")
        fn = _OPS.get(op)
        if fn is None:
            resp = {"ok": False, "id": rid, "error": f"unknown op {op!r}"}
        else:
            try:
                resp = {"id": rid, **fn(**{k: v for k, v in req.items()
                                          if k not in ("id", "op")})}
            except Exception as exc:  # a broker op never takes the loop down
                resp = {"ok": False, "id": rid, "error": f"{exc}"[:300]}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
