"""tests/test_attachments.py — composer attachments (images/video/docs).

The user-facing bug this pins: the composer accepted attachments in the
UI, but the bridge silently dropped them (GoalRequest had no
attachments field) and pasted/dragged media had no path in the
renderer at all (Electron 43 removed File.path). The contract now:

1. GoalRequest accepts `attachments: list[str]`.
2. `_sanitize_attachments` keeps only real files (≤8, case-insensitive
   dedupe, stale paths dropped — never a 422 for the user's send).
3. `_text_with_attachments` wraps the paths in one fenced block that
   names the tools the agent must use (VisionAnalyze / shell ffprobe)
   and forbids inventing content it did not read.
"""
from __future__ import annotations

from universe_desktop_bridge.app import (
    GoalRequest,
    _sanitize_attachments,
    _text_with_attachments,
)


def test_goal_request_accepts_attachments():
    r = GoalRequest(text="look at this", attachments=["C:\\tmp\\shot.png"])
    assert r.attachments == ["C:\\tmp\\shot.png"]
    # absent stays absent — older clients unaffected
    r2 = GoalRequest(text="plain message")
    assert r2.attachments == []


def test_sanitize_drops_missing_and_dedupes(tmp_path):
    real1 = tmp_path / "shot.png"
    real1.write_bytes(b"\x89PNG\r\n\x1a\n")  # real, non-empty file
    real2 = str(tmp_path / "clip.mp4")
    # NOTE: real2 intentionally never created — a stale picker path
    p1 = str(real1)
    out = _sanitize_attachments([
        p1,
        str(real2),           # does not exist → dropped
        p1.upper(),           # case-insensitive duplicate → dropped
        '',                   # empty → dropped
        '   ',                # whitespace → dropped
    ])
    assert out == [p1]


def test_sanitize_caps_at_eight(tmp_path):
    paths = []
    for i in range(12):
        p = tmp_path / f"f{i}.png"
        p.write_bytes(b"x")
        paths.append(str(p))
    out = _sanitize_attachments(paths)
    assert len(out) == 8

def test_sanitize_empty_file_dropped(tmp_path):
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")          # zero bytes → dropped
    real = tmp_path / "real.png"
    real.write_bytes(b"data")
    assert _sanitize_attachments([str(empty), str(real)]) == [str(real)]


def test_text_with_attachments_block():
    text = _text_with_attachments(
        "analyze this", [r"C:\tmp\shot.png", r"C:\tmp\clip.mp4"]
    )
    assert text.startswith("analyze this\n\n---\nAttached files")
    assert r"- C:\tmp\shot.png" in text
    assert r"- C:\tmp\clip.mp4" in text
    assert "VisionAnalyze" in text          # the image tool is named
    assert "ffprobe" in text               # the video path is named
    assert "never claim" in text           # the honesty rule rides along
