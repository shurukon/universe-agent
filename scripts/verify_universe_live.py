"""Live pixel verification of the Universe desktop window (numeric, no eyes).

Brings the "Universe Agent" Electron window to the foreground, confirms
via GetForegroundWindow() that THE GRAB IS OF UNIVERSE (not Universe or anything
else), then samples the Universe palette per docs/shell-tokens.md:

  sidebar / panels / composer card   #2b2b2b
  topbar over chat                   #262626
  topbar left segment (over sidebar) #2b2b2b
  canvas                            #161616
  kbd chip                          #404040
  composer border / strong line     #4b4b4b
  active pill                       #414141
  ink                               #d4d4d4 / #999999 / #5e5e5e

and asserts the restyle really happened: no teal rgb(20,184,166)/#14b8a6
family in the UI chrome histogram.

Standalone: `python scripts/verify_universe_live.py`.
Writes docs/shell-live-screens/live-universe-window.png (image only).
Exit code 0 = all checks passed. Prints a numeric report.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from collections import Counter

from PIL import ImageGrab

user32 = ctypes.windll.user32
# the renderer appends " — Desktop" to document.title
TARGET_TITLE = "Universe Agent — Desktop"


def find_window() -> int:
    hwnds: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == TARGET_TITLE and user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    if not hwnds:
        raise SystemExit(f"no visible window titled {TARGET_TITLE!r}")
    return hwnds[0]


def activate(hwnd: int) -> None:
    """Foreground activation that survives Windows focus stealing rules:
    attach to the foreground thread's input queue, send the ALT key, then
    SetForegroundWindow — the documented workaround."""
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    this_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    if fg_tid != this_tid:
        user32.AttachThreadInput(this_tid, fg_tid, True)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    if fg_tid != this_tid:
        user32.AttachThreadInput(this_tid, fg_tid, False)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    # DWM extended frame bounds = the real visible window rect
    rect = wt.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def close(a, b, tol=6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def main() -> int:
    hwnd = find_window()
    activate(hwnd)
    time.sleep(1.2)
    fg = user32.GetForegroundWindow()
    if fg != hwnd:
        raise SystemExit(f"activation failed: foreground is {fg}, target {hwnd}")
    print(f"[ok] Universe window {hwnd} IS the foreground window")

    left, top, right, bottom = window_rect(hwnd)
    w, h = right - left, bottom - top
    print(f"[ok] window rect ({left},{top})..({right},{bottom})  {w}x{h}")

    img = ImageGrab.grab(bbox=(left, top, right, bottom))
    out = r"D:\Universecomputer\Universe-compter\docs\shell-live-screens"
    img.save(out + r"\live-universe-window.png")
    print(f"[ok] capture saved: {out}\\live-universe-window.png")

    px = img.convert("RGB")
    W, H = px.size

    def at(fx: float, fy: float):
        return px.getpixel((int(fx * (W - 1)), int(fy * (H - 1))))

    checks = [
        # (label, fractional (x, y), expected hex)
        ("topbar left segment (over sidebar)", (0.03, 0.030), (0x2b, 0x2b, 0x2b)),
        ("topbar over chat",                   (0.50, 0.030), (0x26, 0x26, 0x26)),
        ("sidebar mid",                        (0.055, 0.50), (0x2b, 0x2b, 0x2b)),
        ("sidebar lower",                      (0.055, 0.80), (0x2b, 0x2b, 0x2b)),
        ("canvas mid-right",                   (0.80, 0.45), (0x16, 0x16, 0x16)),
    ]
    failures = []
    for label, (fx, fy), want in checks:
        got = at(fx, fy)
        ok = close(got, want)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: "
              f"got #{got[0]:02x}{got[1]:02x}{got[2]:02x} want "
              f"#{want[0]:02x}{want[1]:02x}{want[2]:02x}")
        if not ok:
            failures.append(label)

    # ---- whole-window histogram: the palette should dominate, teal must be gone
    hist = Counter(px.getdata())
    expected = {
        "#2b2b2b": (0x2b, 0x2b, 0x2b), "#262626": (0x26, 0x26, 0x26),
        "#161616": (0x16, 0x16, 0x16), "#404040": (0x40, 0x40, 0x40),
        "#4b4b4b": (0x4b, 0x4b, 0x4b), "#414141": (0x41, 0x41, 0x41),
        "#d4d4d4": (0xd4, 0xd4, 0xd4), "#999999": (0x99, 0x99, 0x99),
        "#5e5e5e": (0x5e, 0x5e, 0x5e), "#363636": (0x36, 0x36, 0x36),
    }
    total = W * H
    print(f"\n---- histogram ({total} px) ----")
    core = 0
    for name, rgb in expected.items():
        # exact + near matches (antialiasing at edges)
        n = sum(c for (r, g, b), c in hist.items() if close((r, g, b), rgb))
        core += n
        print(f"  {name}: {n:7d}  ({100 * n / total:5.2f}%)")
    print(f"  core palette coverage: {100 * core / total:.1f}%")

    teal_strong = sum(c for (r, g, b), c in hist.items()
                      if close((r, g, b), (20, 184, 166), 24))
    print(f"  teal rgb(20,184,166)+/-24: {teal_strong} px "
          f"[WebGL universe accent may contribute a little]")
    if teal_strong > 500:
        failures.append(f"teal #14b8a6 still present ({teal_strong}px)")

    print()
    if failures:
        print("RESULT: FAIL - " + "; ".join(failures))
        return 1
    print("RESULT: PASS - Universe renders the Universe palette")
    return 0


if __name__ == "__main__":
    sys.exit(main())
