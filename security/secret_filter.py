"""security/secret_filter.py — output redaction for logs (§10).

Pattern-matched redaction at every log boundary. The filtered output is what
gets logged AND shown to the user. Plus entropy-based detection for unknown
secret shapes.
"""
from __future__ import annotations

import math
import re
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                              # OpenAI / most providers
    re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),                         # Anthropic
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),                             # GitHub PAT
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),                     # Slack
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),                            # Google API key
    re.compile(r"eyJ[A-Za-z0-9_=-]+\.eyJ[A-Za-z0-9_=-]+\.?[A-Za-z0-9_.+/=-]*"),  # JWT
    re.compile(r"Bearer\s+[A-Za-z0-9_.-]{20,}"),                     # Bearer tokens
]


# key=value credential shapes — the captured VALUE is substantial (12+)
# so config booleans/short enums are never touched. Group(1) keeps the
# `key=` prefix visible for debugging while the value is redacted.
_KEYVALUE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|access[_-]?key)"
    r"(\s*[:=]\s*[\"']?)([A-Za-z0-9_+/=-]{12,})")

# credentials embedded in a URL: scheme://user:password@host — the
# password part must go, the rest of the URL must stay usable
_URLCREDS = re.compile(r"([a-z][a-z0-9+.-]*://[^/@:\s]+:)([^@/\s]{4,})@")


_REDACTED = "[REDACTED]"

# Canonical hyphenated UUID (8-4-4-4-12). Universe's internal ids are exactly
# this shape ("goal-<uuid4>", "planet-<uuid4>", …) and ~27% of them cross the
# entropy threshold as whole tokens — a UUID is an identifier, never a secret.
_UUID = re.compile(
    r"^[A-Za-z0-9]+(-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")

# A 32+ char pure-hex blob is digest/signature/secret material — redact
# regardless of entropy score (all-hex caps its entropy near 4 bits/char,
# so the generic threshold misses exactly these).
_HEX_BLOB = re.compile(r"^[0-9a-fA-F]{32,}$")


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _looks_like_secret(token: str) -> bool:
    """Entropy-based detection for unknown secret shapes (§10).

    A digit is required: real credential material (api keys, base64/hex
    tokens, passwords) carries digits essentially always, while prose and
    path-like text ("Build/Run/Verify") has exactly this length/class
    profile with no digits — the previous classes>=3 rule ate plan and
    todo text as false positives."""
    if len(token) < 20:
        return False
    if not any(c.isdigit() for c in token):
        return False
    # a digit is proven above — the class tally starts with it banked
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    has_special = any(not c.isalnum() for c in token)
    classes = 1 + sum([has_upper, has_lower, has_special])
    return _shannon_entropy(token) > 4.0 and classes >= 3


def redact(text: str) -> str:
    """Apply all secret patterns + entropy detection. Returns redacted text."""
    if not text:
        return text
    out = text
    for pat in SECRET_PATTERNS:
        out = pat.sub(_REDACTED, out)
    # key=value credentials: keep the key name, redact only the value
    out = _KEYVALUE.sub(lambda m: m.group(1) + m.group(2) + _REDACTED, out)
    # URL-embedded credentials: keep scheme://user:, redact the password
    out = _URLCREDS.sub(lambda m: m.group(1) + _REDACTED + "@", out)
    # entropy-based: scan long alphanumeric tokens (hex blobs of 32+ chars
    # are almost always digest/signature material — checked below too)
    def _entropy_sub(m: re.Match) -> str:
        tok = m.group(0)
        if _UUID.match(tok):
            return tok  # internal/external UUID identifiers are not secrets
        if _HEX_BLOB.match(tok):
            return _REDACTED  # 32+ hex chars: treat as secret material
        return _REDACTED if _looks_like_secret(tok) else tok
    out = re.sub(r"[A-Za-z0-9_+/=-]{24,}", _entropy_sub, out)
    return out


def redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact all string values in a dict (including strings
    nested in lists, lists-in-lists, tuples, and dict values of tuples)."""
    def _walk(v: Any) -> Any:
        if isinstance(v, str):
            return redact(v)
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            seq = [_walk(x) for x in v]
            return tuple(seq) if isinstance(v, tuple) else seq
        return v
    return {k: _walk(v) for k, v in d.items()}
