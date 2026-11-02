"""universe_desktop_bridge/wake.py — the custom wake word ("wake up L").

The user names their universe anything ("wake up L"). Dedicated keyword-spot
models can't know an arbitrary phrase without training, and openwakeword's
training path needs torch + sample collection — so v1 detects the custom
phrase with the SAME local STT that transcribes goals:

  VAD-gated voiced segment → tiny/base whisper → fuzzy phrase match

Zero training, exact custom phrase, honest latency (end of segment). A
dedicated .onnx KWS loader was scaffolding that no runtime path ever
called — the STT matcher is the only mechanism, stated plainly.
"""
from __future__ import annotations

import difflib
import re

THRESHOLD = 0.62  # fuzzy similarity required for a wake match
MIN_WAKE_S = 0.5  # segments shorter than this can't contain the phrase


def wake_slug(wake_word: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", wake_word.strip().lower()).strip("-") or "wake-up-l"


def _words(text: str) -> list[str]:
    """Split on whitespace AND hyphens — 'wake-up L' is 'wake up L'."""
    return [w for w in re.split(r"[\s\-]+", text.strip().lower()) if w]


def _name_ok(a: str, b: str) -> bool:
    """The NAME word (usually last) is identity — strict gate.
    Short names ("L") are spoken as "el"/"ell", so a shared first letter
    counts; real names ("nova") require the exact word ("now" ≠ "nova")."""
    if a == b:
        return True
    if len(b) <= 2:
        return a[:1] == b[:1] or b[:1] in a
    return False


def match_score(text: str, wake_word: str) -> float:
    """Normalized fuzzy similarity between a transcript and the wake phrase.
    'wake up el' / 'wake up ell' must match 'wake up L'; a one-letter
    different name ('hey now' vs 'hey nova') must not."""
    phrase = wake_word.strip().lower()
    if not phrase or not text:
        return 0.0
    words = _words(text)
    pw = _words(phrase)
    if len(words) < len(pw):
        return 0.0
    # best sliding window over the transcript
    best = 0.0
    for i in range(len(words) - len(pw) + 1):
        window_words = words[i: i + len(pw)]
        window = " ".join(window_words)
        ratio = difflib.SequenceMatcher(None, window, phrase).ratio()
        # per-word equality dominates: a word that merely *looks* similar
        # (now/nova) can't carry the score over the threshold
        word_eq = sum(1 for a, b in zip(window_words, pw) if a == b) / len(pw)
        combined = 0.6 * ratio + 0.4 * word_eq
        # the name word is identity: without it there is no wake
        if not _name_ok(window_words[-1], pw[-1]):
            combined = min(combined, THRESHOLD * 0.8)
        best = max(best, combined)
    return best


def is_wake(text: str, wake_word: str) -> bool:
    return match_score(text, wake_word) >= THRESHOLD


def strip_wake(text: str, wake_word: str) -> str:
    """Remove the wake phrase from the start of a transcript (when the user
    says the phrase and the goal in one breath)."""
    words = text.split()
    pw = _words(wake_word)
    lowered = [w.strip(".,!?").lower() for w in words]
    if len(lowered) >= len(pw):
        head = " ".join(lowered[: len(pw)])
        if match_score(head, wake_word) >= THRESHOLD:
            return " ".join(words[len(pw):]).strip()
    return text.strip()
