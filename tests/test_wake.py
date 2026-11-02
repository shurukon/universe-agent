"""tests/test_wake.py — the custom wake-phrase detector (§8).

The phrase is arbitrary ("wake up L" — the user names their universe), so
detection is fuzzy-matching over a local whisper transcript. These tests
pin the exact matching behavior: real recognition noise ("wake up el",
"wakeop L") must wake; ordinary speech must not; a goal spoken in the same
breath keeps only the part after the phrase.
"""
from __future__ import annotations

from universe_desktop_bridge.wake import (
    THRESHOLD, is_wake, match_score, strip_wake, wake_slug,
)


def test_exact_phrase_wakes():
    assert is_wake("wake up L", "wake up L")
    assert is_wake("Wake up L.", "wake up L")


def test_recognition_noise_still_wakes():
    # whisper commonly renders "wake up L" as:
    assert is_wake("wake up el", "wake up L")
    assert is_wake("wake up ell", "wake up L")
    assert is_wake("wake up, L", "wake up L")
    assert is_wake(" Wake up L ", "wake up L")
    assert is_wake("wake-up L", "wake up L")


def test_ordinary_speech_does_not_wake():
    assert not is_wake("write a hello function in python", "wake up L")
    assert not is_wake("what's the weather", "wake up L")
    assert not is_wake("wake", "wake up L")               # too short
    assert not is_wake("look up LLM papers", "wake up L")  # near-miss words


def test_phrase_inside_longer_transcript_still_wakes():
    # user says the phrase then keeps talking before pausing
    assert is_wake("wake up L and then write me a poem", "wake up L")
    assert match_score("um wake up L please", "wake up L") >= THRESHOLD


def test_strip_wake_keeps_only_the_goal():
    assert strip_wake("wake up L write a poem about mars", "wake up L") == \
        "write a poem about mars"
    assert strip_wake("wake up el and then rename my files", "wake up L") == \
        "and then rename my files"
    # no wake phrase: the text is the goal as-is
    assert strip_wake("write a poem", "wake up L") == "write a poem"


def test_custom_names_work():
    assert is_wake("hey nova", "hey nova")
    assert not is_wake("hey now", "hey nova")


def test_slug_is_deterministic(fresh_home):
    assert wake_slug("Wake Up L!") == "wake-up-l"
    assert wake_slug("wake up L") == wake_slug("WAKE UP L")
