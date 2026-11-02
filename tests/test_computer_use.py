"""tests/test_computer_use.py — the Phase D broker + connector.

Drives universe_cua_helper as a REAL subprocess over NDJSON (the same
protocol the bridge-side tools speak) and covers the safety rails:
frame binding (stale/unknown/out-of-bounds frames refuse coordinate
actions), the a11y ops, and the tool registration contract
(capability=machine.control, consent=explicit — the consent banner owns
the grant, the broker never decides policy).

Needs a real interactive Windows session; skips honestly elsewhere.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="computer-use broker is Windows-only")


class Broker:
    """Test-side NDJSON client — mirrors _Broker in computer_use.py."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "universe_cua_helper"],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace")
        self.seq = 0

    def __call__(self, op: str, **args) -> dict:
        self.seq += 1
        rid = self.seq
        self.proc.stdin.write(json.dumps({"id": rid, "op": op, **args}) + "\n")
        self.proc.stdin.flush()
        for _ in range(200):  # ~25s of line budget
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("broker died")
            resp = json.loads(line)
            if resp.get("id") == rid:
                return resp
        raise RuntimeError("broker never answered")

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


@pytest.fixture(scope="module")
def broker():
    b = Broker()
    caps = b("capabilities")
    if not caps.get("ok"):
        b.close()
        pytest.skip("no interactive session for the broker")
    yield b
    b.close()


# ---- protocol ----------------------------------------------------------------

def test_capabilities_reports_the_machine(broker):
    r = broker("capabilities")
    assert r["ok"] is True
    assert r["helper"] == "universe_cua_helper"
    assert r["frame_ttl_s"] == 15
    assert r["displays"] >= 1
    assert r["input_model"]["text_via"] == "uia_value_pattern"


def test_windows_lists_real_windows(broker):
    r = broker("windows")
    assert r["ok"] is True
    assert len(r["windows"]) >= 1
    w = r["windows"][0]
    for key in ("hwnd", "pid", "title", "rect"):
        assert key in w


def test_unknown_op_is_rejected_without_dying(broker):
    r = broker("bogus_op")
    assert r["ok"] is False and "unknown op" in r["error"]
    # the loop survived: the next op answers
    assert broker("capabilities")["ok"] is True


# ---- frames + binding -----------------------------------------------------------

def test_screenshot_makes_a_live_frame(broker):
    r = broker("screenshot")
    assert r["ok"] is True
    assert r["frame_id"].startswith("frame-")
    assert r["width"] > 100 and r["height"] > 100
    assert os.path.isfile(r["path"])
    assert r["expires_at"] > 0


def test_click_requires_a_known_frame(broker):
    r = broker("mouse", frame_id="frame-does-not-exist", x=10, y=10)
    assert r["ok"] is False and "frame" in r["error"].lower()


def test_click_outside_the_frame_is_refused(broker):
    shot = broker("screenshot")
    r = broker("mouse", frame_id=shot["frame_id"],
               x=shot["width"] + 500, y=10)
    assert r["ok"] is False and "outside the frame" in r["error"]


def test_click_inside_a_live_frame_works(broker):
    """One real left-click at a harmless spot: the desktop's empty area at
    the bottom-center of the screen (selects nothing). Proves the full
    broker path: frame → bound coordinates → SendInput."""
    shot = broker("screenshot")
    x, y = shot["width"] // 2, shot["height"] - 40
    r = broker("mouse", frame_id=shot["frame_id"], x=x, y=y, kind="click")
    assert r["ok"] is True and r["kind"] == "click"


def test_zoom_crops_a_live_frame(broker):
    shot = broker("screenshot")
    r = broker("zoom", frame_id=shot["frame_id"],
               x=0, y=0, w=200, h=120)
    assert r["ok"] is True
    assert os.path.isfile(r["path"])
    assert r["width"] >= 200  # upscaled for legibility


def test_scroll_and_drag_are_frame_bound(broker):
    shot = broker("screenshot")
    ok = broker("scroll", frame_id=shot["frame_id"],
                x=shot["width"] // 2, y=shot["height"] // 2, amount=-1)
    assert ok["ok"] is True
    bad = broker("drag", frame_id="frame-none", x1=1, y1=1, x2=2, y2=2)
    assert bad["ok"] is False


# ---- a11y -----------------------------------------------------------------------

def test_app_state_needs_a_window(broker):
    r = broker("app_state")
    assert r["ok"] is False


def test_app_state_reads_a_real_window(broker):
    wins = broker("windows")["windows"]
    target = next((w for w in wins if w["title"]), None)
    assert target is not None
    r = broker("app_state", hwnd=target["hwnd"], max_nodes=40)
    assert r["ok"] is True
    assert isinstance(r["controls"], list)


def test_set_value_on_missing_window_is_honest(broker):
    r = broker("set_value", title="no-such-window-xyz", value="hi")
    assert r["ok"] is False


# ---- the tool contract ------------------------------------------------------------

def test_tools_register_with_explicit_consent():
    """ZCode CUA names (RE map): the machine.control set is registered under
    the names ZCode's computer-use plugin exposes — no legacy computer.* set
    remains."""
    from connectors.builtin import reset_registry_for_tests
    reg = reset_registry_for_tests()
    names = [n for n in reg.names()
             if n in ("Screenshot", "LeftClick", "RightClick", "DoubleClick",
                      "LongPress", "Swipe", "Scroll", "SetValue",
                      "OpenApplication", "SwitchDisplay", "RequestAccess",
                      "ListApps", "ListWindows", "GetAppState", "Zoom")]
    assert len(names) >= 14
    for n in names:
        tool = reg.get(n)
        assert tool.capability == "machine.control"
        assert tool.consent == "explicit"
    # the Phase B TransparencyBar rides these very names
    for expected in ("Screenshot", "LeftClick", "SetValue", "RequestAccess"):
        assert expected in names
