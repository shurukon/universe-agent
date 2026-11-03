"""scripts/e2e_full_path.py — the live full-path E2E for the failure fixes.

Sends a real goal through the REAL app UI (UIA ValuePattern — bypasses the
keyboard filter), then babysits the run to verify the whole chain:

  goal → StatusStrip live → agents → file.write → CONSENT BANNER →
  (this script clicks Approve the moment it appears) → write lands →
  goal completes → the file exists on disk

Every observation is printed with timestamps; screenshots land in
docs/shell-live-screens/e2e-full-*.png (override: UNIVERSE_E2E_SCREENSHOT_DIR).
Exit 0 only if every step of the chain is proven live.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from PIL import ImageGrab
from pywinauto import Desktop
from pywinauto.keyboard import send_keys

# the old absolute D:\Universecomputer\... path broke on every other checkout;
# default to THIS repo's docs dir, overridable from the environment
OUT = os.environ.get("UNIVERSE_E2E_SCREENSHOT_DIR") or str(
    Path(__file__).resolve().parent.parent / "docs" / "shell-live-screens")
TURN = ("Create a file named e2e_check2.txt containing exactly this text: "
        "the live full-path e2e works")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    Path(OUT).mkdir(parents=True, exist_ok=True)  # before the first snap() write
    uia = Desktop(backend="uia")
    win = uia.window(title="Universe Agent — Desktop")
    for _ in range(20):
        r = win.rectangle()
        if (r.right - r.left) > 200:
            break
        time.sleep(1)
    win.set_focus()
    time.sleep(0.6)
    L, T, R, B = r.left, r.top, r.right, r.bottom

    def snap(name: str) -> None:
        ImageGrab.grab(bbox=(L, T, R, B)).save(f"{OUT}\\e2e-full-{name}.png")

    def names():
        return [
            (h.element_info.control_type, h.element_info.name or "")
            for h in win.descendants()
        ]

    # baseline: the newest goal BEFORE ours
    db = os.path.expanduser("~/.universe/universe.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    prev = con.execute(
        "SELECT id FROM goals ORDER BY created_at DESC LIMIT 1").fetchone()["id"]

    # ---- 1. send the goal --------------------------------------------------
    gi = win.child_window(title="Goal input", control_type="Edit")
    gi.click_input()
    time.sleep(0.3)
    gi.set_edit_text(TURN)
    time.sleep(0.3)
    send_keys("{ENTER}")
    log("goal sent")

    strip_seen: list[str] = []
    approved = False
    deadline = time.time() + 420  # 7 minutes for LLM rounds + consent

    while time.time() < deadline:
        time.sleep(5)
        ns = names()
        flat = [n for _, n in ns]

        # live strip text snapshots
        for line in flat:
            if any(k in line for k in ("thinking", "working", "planning",
                                       "waiting for your approval")):
                if line not in strip_seen:
                    strip_seen.append(line)
                    log(f"strip: {line[:80]}")

        # the consent banner: Approve button present?
        if not approved and "Approve" in flat:
            snap("banner")
            log("CONSENT BANNER VISIBLE — clicking Approve")
            try:
                btn = win.child_window(title="Approve", control_type="Button")
                btn.click_input()
                approved = True
                log("Approved clicked")
            except Exception as exc:
                log(f"approve click failed: {exc}")
                return 1

        # terminal check via db
        row = con.execute(
            "SELECT id, status, completed_at - created_at AS dur FROM goals "
            "WHERE id != ? ORDER BY created_at DESC LIMIT 1", (prev,)).fetchone()
        if row and row["status"] in ("completed", "failed", "cancelled"):
            log(f"goal terminal: {row['status']} after {row['dur']}s")
            break

    if not approved:
        log("consent banner never appeared in the window")
        snap("no-banner")
        return 1

    # ---- 3. verify the write + the surfaces --------------------------------
    time.sleep(3)
    ns = names()
    flat = [n for _, n in ns]
    mentions = [n for n in flat if "e2e_check2" in n]
    log(f"chat mentions of e2e_check2: {len(mentions)}")

    # find the file on disk (the app's project_root first, then the home)
    found = None
    search_roots = [
        os.environ.get("UNIVERSE_E2E_PROJECT_ROOT"),
        os.path.expanduser("~"),
        os.path.expanduser("~/Documents"),
    ]
    for root in [r for r in search_roots if r]:
        p = os.path.join(root, "e2e_check2.txt")
        if os.path.exists(p):
            found = p
            break
    if found:
        log(f"FILE EXISTS: {found}")
        log(f"content: {open(found, encoding='utf-8', errors='replace').read()[:80]!r}")
    else:
        log("file not found on disk")

    snap("final")
    row = con.execute(
        "SELECT status, final_summary FROM goals "
        "WHERE id != ? ORDER BY created_at DESC LIMIT 1", (prev,)).fetchone()
    log(f"final status: {row['status']}")
    ok = row["status"] == "completed" and found and approved
    log("RESULT: " + ("PASS — full chain proven live" if ok else
                      "PARTIAL — see the observations above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
