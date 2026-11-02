"""skill/signing.py — SHA-256 skill signing + re-verification.

§10, §18, §25 Phase 10 53. At ingest, Universe computes a SHA-256 of each
skill's normalized content. For trusted sources, the hash is stored and
re-verified weekly by the Subconscious Loop (protects against the upstream
repo silently changing after Universe already vetted it). For community skills,
a hash mismatch on re-fetch quarantines the skill and notifies the user
rather than silently accepting changed code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize(content: str) -> str:
    """Stable normalization so trivial whitespace changes don't break the hash,
    but real content changes do."""
    return "\n".join(line.rstrip() for line in content.splitlines()).strip() + "\n"


def sign(content: str) -> str:
    """SHA-256 of normalized content."""
    return hashlib.sha256(normalize(content).encode("utf-8")).hexdigest()


def sign_skill(name: str, source: str, version: str, body: str,
               frontmatter: dict[str, Any] | None = None) -> str:
    """Sign the canonical skill representation. The signature covers name,
    source, version, and the normalized body (the substantive content the
    Subconscious Loop re-verifies weekly). Frontmatter metadata is excluded
    so adding a tag doesn't break the signature — only a content change does."""
    payload = {
        "name": name,
        "source": source,
        "version": version,
        "body": normalize(body),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify(content: str, expected_signature: str) -> bool:
    return sign(content) == expected_signature


def verify_skill(name: str, source: str, version: str, body: str,
                 frontmatter: dict[str, Any] | None,
                 expected_signature: str) -> bool:
    return sign_skill(name, source, version, body, frontmatter) == expected_signature
