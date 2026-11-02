"""skill/quarantine.py — quarantine tier for community skills (§18).

Anything outside the trusted allowlist (§5) lands here. Surfaced in
/quarantine for per-skill user approval. Quarantined skills are prompt
content — approval promotes the reviewed text as an indexed skill at
lowered confidence; it is never executed as code. The Docker backend
(security/sandbox.py) is available for shell/tool sandboxing, not as a
skill sandbox. Requires explicit per-skill approval.
"""
from __future__ import annotations

import json
import time
from typing import Any

from core.agent.base_agent import new_id
from core.memory import get_memory
from core.memory.layers.l4_procedural import Skill
from storage.local import get_storage


def submit_community_skill(*, name: str, source_url: str, normalized: dict[str, Any],
                           signature: str) -> str:
    """Submit a community skill to the quarantine queue. Returns the quarantine id."""
    qid = new_id("quarantine-")
    st = get_storage()
    with st.transaction() as conn:
        conn.execute(
            "INSERT INTO skill_quarantine(id,name,source_url,normalized,signature,"
            "submitted_at,approved,reviewed_at) VALUES(?,?,?,?,?,?,0,NULL);",
            (qid, name, source_url, json.dumps(normalized), signature, time.time()),
        )
    return qid


def list_quarantine() -> list[dict[str, Any]]:
    st = get_storage()
    rows = st.query_all("SELECT * FROM skill_quarantine ORDER BY submitted_at DESC;")
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["normalized"] = json.loads(r["normalized"] or "{}")
        except Exception:
            d["normalized"] = {}
        out.append(d)
    return out


def approve(quarantine_id: str, *, confidence: float | None = None) -> Skill | None:
    """Approve a quarantined skill -> promote to L4.

    Quarantined skills are PROMPT CONTENT — approval promotes the reviewed
    text as an indexed skill; it is never executed as code (see §6/§7 of
    docs/SECURITY.md). A skill whose source is on the trusted allowlist is
    promoted as 'trusted' at the trusted-load confidence for its tier (this
    is how restored/updated bundled content comes back through review);
    anything else becomes an 'active' community skill at the given
    confidence (default 0.70 — lower than the trusted 0.90–0.95).

    User-owned state (row id, enabled toggle, use_count, last_used,
    created_at) survives the promotion when a row already exists for the
    same (name, source) — approval replaces content, not the user's
    relationship with the skill."""
    st = get_storage()
    row = st.query_one("SELECT * FROM skill_quarantine WHERE id=?;", (quarantine_id,))
    if not row:
        return None
    norm = json.loads(row["normalized"] or "{}")
    source = row.get("source_url") or "community"
    from config import TRUSTED_SKILL_SOURCES, SKILL_CONFIDENCE
    if source in TRUSTED_SKILL_SOURCES:
        status = "trusted"
        if confidence is None:
            # ECC + UIUXProMax -> 0.95; other trusted sources -> 0.90 (§5)
            confidence = (SKILL_CONFIDENCE["trusted_load"]
                          if source in ("ECC", "UIUXProMax")
                          else SKILL_CONFIDENCE["trusted_load_lower"])
    else:
        status = "active"
        if confidence is None:
            confidence = 0.70
    mem = get_memory()
    existing = mem.l4.find_by_name(row["name"], source)
    skill = Skill(
        id=existing.id if existing else new_id("skill-"),
        name=row["name"], source=source,
        version=norm.get("version", "1.0.0"), description=norm.get("description", ""),
        body=norm.get("body", ""), tags=norm.get("tags", []), triggers=norm.get("triggers", []),
        license=norm.get("license", "unknown"), confidence=confidence,
        status=status, signature=row.get("signature") or "",
        category=norm.get("category", "general"),
        target_agent=norm.get("target_agent", ""),
        last_used=existing.last_used if existing else 0.0,
        last_verified=time.time(),
        use_count=existing.use_count if existing else 0,
        needs_review=False,
        created_at=existing.created_at if existing and existing.created_at else time.time(),
        enabled=existing.enabled if existing else True,
    )
    mem.l4.upsert(skill)
    with st.transaction() as conn:
        conn.execute(
            "UPDATE skill_quarantine SET approved=1, reviewed_at=? WHERE id=?;",
            (time.time(), quarantine_id),
        )
    return skill


def reject(quarantine_id: str) -> bool:
    st = get_storage()
    with st.transaction() as conn:
        cur = conn.execute("DELETE FROM skill_quarantine WHERE id=?;", (quarantine_id,))
        return cur.rowcount > 0


def pending_count() -> int:
    st = get_storage()
    row = st.query_one("SELECT COUNT(*) AS c FROM skill_quarantine WHERE approved=0;")
    return int(row["c"]) if row else 0
