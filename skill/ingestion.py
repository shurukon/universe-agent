"""skill/ingestion.py — the 8-step ingestion pipeline (§18).

discover → parse → normalize → dedup → sign → quarantine-check → index → audit

This pipeline runs identically whether a skill is from a trusted source (§5)
or a community contribution. The only difference is the quarantine-check
step: trusted sources land in L4 at 0.90–0.95; anything else lands in the
Quarantine tier for per-skill user approval (and into the skill_quarantine
review queue).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from config import TRUSTED_SKILL_SOURCES, SKILL_CONFIDENCE, get_config
from core.memory.layers.l4_procedural import Skill
from core.memory import get_memory
from skill.signing import sign_skill
from skill.conflict import dedup_skills, resolve_conflicts
from storage.local import get_storage


def skills_data_root() -> Path:
    """Root of the bundled skill corpus (skills_data/ next to the package).

    UNIVERSE_SKILLS_DATA overrides the package-relative location — an
    advanced/test seam so the bootstrap can be pointed at an alternate copy
    of the corpus (tamper tests run against a temp copy, never the repo).
    """
    override = os.environ.get("UNIVERSE_SKILLS_DATA")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "skills_data"


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML-ish frontmatter (key: value lines between --- fences)."""
    fm: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        parts = text[3:].split("---", 1)
        if len(parts) == 2:
            for line in parts[0].strip().splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    v = v.strip().strip('"').strip("'")
                    # parse lists like ["a", "b"]
                    if v.startswith("[") and v.endswith("]"):
                        try:
                            fm[k.strip()] = json.loads(v)
                        except Exception:
                            fm[k.strip()] = [x.strip().strip('"').strip("'")
                                             for x in v[1:-1].split(",") if x.strip()]
                    else:
                        fm[k.strip()] = v
            body = parts[1].strip()
    return fm, body


def discover(source_dir: Path) -> list[Path]:
    """Collect every .md skill file under source_dir."""
    if not source_dir.exists():
        return []
    return sorted(source_dir.rglob("*.md"))


def parse(path: Path) -> dict[str, Any]:
    """Parse a skill file into a raw record."""
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    return {
        "name": fm.get("name", path.stem),
        "source": fm.get("source", _source_from_path(path)),
        "version": fm.get("version", "1.0.0"),
        "description": fm.get("description", ""),
        "body": body,
        "tags": fm.get("tags", []),
        "triggers": fm.get("triggers", []),
        "license": fm.get("license", "MIT"),
        "target_agent": fm.get("target_agent", ""),
        "category": fm.get("category", "general"),
        "frontmatter": fm,
        "path": str(path),
    }


def _source_from_path(path: Path) -> str:
    """skills_data/ECC/python/foo.md -> ECC."""
    try:
        return path.relative_to(get_config().home.parent).parts[0]
    except Exception:
        return path.parts[-3] if len(path.parts) >= 3 else "unknown"


def normalize_skill(raw: dict[str, Any]) -> Skill:
    """Convert a raw parsed record into Universe's Skill schema."""
    return Skill(
        name=raw["name"], source=raw["source"], version=raw.get("version", "1.0.0"),
        description=raw.get("description", ""), body=raw.get("body", ""),
        tags=list(raw.get("tags", [])), triggers=list(raw.get("triggers", [])),
        license=raw.get("license", "MIT"),
        confidence=SKILL_CONFIDENCE["trusted_load"],
        status="trusted" if raw["source"] in TRUSTED_SKILL_SOURCES else "quarantine",
        signature=sign_skill(raw["name"], raw["source"], raw.get("version", "1.0.0"),
                             raw.get("body", ""), raw.get("frontmatter")),
        category=raw.get("category", "general"),
        target_agent=raw.get("target_agent", ""),
        last_used=0.0, last_verified=time.time(), use_count=0, needs_review=False,
        created_at=time.time(),
    )


def quarantine_check(skill: Skill) -> Skill:
    """Trusted source -> L4 at 0.90–0.95. Anything else -> quarantine tier."""
    if skill.source in TRUSTED_SKILL_SOURCES:
        # ECC + UIUXProMax -> 0.95; OpenDesign + AnthropicSkills -> 0.90 (§5)
        if skill.source in ("ECC", "UIUXProMax"):
            skill.confidence = SKILL_CONFIDENCE["trusted_load"]  # 0.95
        else:
            skill.confidence = SKILL_CONFIDENCE["trusted_load_lower"]  # 0.90
        skill.status = "trusted"
    else:
        skill.confidence = 0.40
        skill.status = "quarantine"
        skill.needs_review = True
    return skill


def _content_fingerprint(skill: Skill) -> str:
    """Version-independent content hash (name + source + body). The stored
    signature covers the version too, but version is user-owned (pin flows
    write it in place), so re-ingest decisions compare content separately
    from the version dimension."""
    return sign_skill(skill.name, skill.source, "", skill.body)


def _preserve_user_state(incoming: Skill, existing: Skill) -> Skill:
    """Copy user-owned / lifecycle state from the stored row onto the freshly
    parsed skill before the upsert, so re-ingestion never wipes it.

    Preserved (user-owned): row identity (id — skill_activations and the
    taxonomy audit reference it), the Settings enabled toggle, use_count,
    last_used, needs_review, created_at, and the pinned version (pin flows
    write `version` in place; the signature is re-signed over the kept
    version so (name, source, version, body) stays verifiable).
    Updated (content-derived): body, signature, description, tags, triggers,
    license, category, target_agent, and confidence-on-load for trusted
    sources only — an approved community skill keeps its confidence/status.
    """
    incoming.id = existing.id
    incoming.enabled = existing.enabled
    incoming.use_count = existing.use_count
    incoming.last_used = existing.last_used
    incoming.created_at = existing.created_at or incoming.created_at
    incoming.last_verified = existing.last_verified or incoming.last_verified
    incoming.needs_review = existing.needs_review
    if existing.version != incoming.version:
        # version is user-owned (pin_version / /skill --pin write it in
        # place); keep it and re-sign so verification stays consistent
        incoming.version = existing.version
        incoming.signature = sign_skill(incoming.name, incoming.source,
                                        existing.version, incoming.body)
    if incoming.source not in TRUSTED_SKILL_SOURCES:
        # an explicitly approved (or user-authored) community skill keeps its
        # approval across re-ingestion — a reload must not silently demote it
        if existing.status == "active":
            incoming.status = "active"
            incoming.confidence = existing.confidence
    return incoming


def _reingest_decision(incoming: Skill, existing: Skill | None) -> tuple[str, Skill | None]:
    """Decide what a re-ingested skill may do to its stored row (tamper
    defense: the self-healing bootstrap must never re-sign diverging content
    as trusted). Returns (action, merged_skill_or_None) where action is:

      "update"      — upsert the merged skill (content unchanged, or changed
                      only in version metadata)
      "heal"        — the stored row diverged from its own signature (local
                      tamper / pin drift) but the incoming content matches the
                      vetted signature exactly — restoring is proven safe
      "defer"       — the stored row is still the vetted truth and the
                      incoming content diverges from the vetted signature →
                      keep serving the vetted row; queue the diverging
                      content in /quarantine for explicit per-skill approval
      "skip"        — the row is already flagged (quarantined) and the
                      incoming content still diverges → skip + queue + warn
      "compromised" — the row is no longer self-consistent AND the incoming
                      content diverges → flag the row + queue + warn

    No action ever re-signs a body that differs from the vetted signature as
    trusted. Restoring flagged or diverging skills is operator action:
    /skills --reload re-checks the corpus against the vetted signatures and
    only byte-identical content is restored; /quarantine surfaces whatever
    still diverges.
    """
    if existing is None:
        return "update", incoming
    stored_sig = existing.signature or ""
    vetted = bool(stored_sig) and stored_sig == sign_skill(
        existing.name, existing.source, existing.version, existing.body)
    sig_match = bool(stored_sig) and incoming.signature == stored_sig
    if sig_match:
        # incoming is byte-identical to the originally-vetted content
        # (hash covers body and version): safe to restore a locally-tampered
        # row because the restored bytes are provably the vetted ones
        merged = _preserve_user_state(incoming, existing)
        if not vetted:
            merged.needs_review = False
            merged.last_verified = time.time()
        return ("heal" if not vetted else "update"), merged
    body_same = _content_fingerprint(incoming) == _content_fingerprint(existing)
    if body_same:
        # identical content; only version metadata differs — preserve the
        # stored (possibly pinned) version and re-sign over it
        return "update", _preserve_user_state(incoming, existing)
    # the incoming body genuinely differs from the stored body
    if vetted:
        return "defer", None
    if existing.status == "quarantine":
        return "skip", None
    # hand the EXISTING row back so the caller can flag it
    return "compromised", existing


def _queue_for_review(skill: Skill) -> None:
    """Quarantine-table intake: the /quarantine review used to render empty
    forever because nothing ever inserted rows. A skill whose source is NOT
    on the trusted allowlist — or bundled content diverging from its vetted
    signature — gets a pending skill_quarantine row so the operator can
    approve/reject it explicitly. Deduped per (name, source_url) while a
    review is pending; a re-arrival with changed content refreshes the
    pending row so the operator reviews the current bytes, not stale ones."""
    st = get_storage()
    normalized = {
        "version": skill.version, "description": skill.description,
        "body": skill.body, "tags": list(skill.tags),
        "triggers": list(skill.triggers), "license": skill.license,
        "category": skill.category, "target_agent": skill.target_agent,
    }
    pending = st.query_one(
        "SELECT id, signature FROM skill_quarantine "
        "WHERE name=? AND source_url=? AND approved=0;",
        (skill.name, skill.source))
    if pending:
        if (pending.get("signature") or "") != skill.signature:
            with st.transaction() as conn:
                conn.execute(
                    "UPDATE skill_quarantine SET normalized=?, signature=? "
                    "WHERE id=?;",
                    (json.dumps(normalized, ensure_ascii=False), skill.signature,
                     pending["id"]))
        return
    from skill.quarantine import submit_community_skill
    submit_community_skill(name=skill.name, source_url=skill.source,
                           normalized=normalized, signature=skill.signature)


def index_audit(skills: list[Skill]) -> dict[str, int]:
    """Persist parsed skills to L4 under the re-ingestion policy + audit trail.

    counts keys: ingested, trusted, quarantine, deduped, deferred_changed
    (bundled content changed since vetting — held for operator review),
    skipped_tampered (flagged/diverging rows left untouched).
    """
    from loguru import logger

    mem = get_memory()
    st = get_storage()
    counts = {"ingested": 0, "trusted": 0, "quarantine": 0, "deduped": 0,
              "deferred_changed": 0, "skipped_tampered": 0}
    # dedup by (name, source); keep highest version + highest confidence
    deduped = dedup_skills(skills)
    counts["deduped"] = len(skills) - len(deduped)
    for skill in deduped:
        action, resolved = _reingest_decision(
            skill, mem.l4.find_by_name(skill.name, skill.source))
        if action == "defer":
            counts["deferred_changed"] += 1
            logger.warning(
                "Skill '{}' ({}) changed on disk since its vetted signature "
                "was recorded — NOT re-ingested as trusted; the stored row "
                "keeps serving the vetted content and the changed copy waits "
                "in /quarantine for explicit approval.", skill.name, skill.source)
            _queue_for_review(skill)
            continue
        if action == "skip":
            counts["skipped_tampered"] += 1
            logger.warning(
                "Skill '{}' ({}) is flagged (quarantined) and the corpus copy "
                "still diverges from the vetted signature — re-ingestion "
                "skipped. Restoring requires operator action: fix the corpus "
                "and re-run /skills --reload, or review it in /quarantine.",
                skill.name, skill.source)
            _queue_for_review(skill)
            continue
        if action == "compromised":
            counts["skipped_tampered"] += 1
            logger.warning(
                "Skill '{}' ({}) row no longer matches its own signature and "
                "the corpus copy diverges too — row flagged needs_review + "
                "quarantined; the diverging copy waits in /quarantine.",
                skill.name, skill.source)
            _queue_for_review(skill)
            with st.transaction() as conn:
                conn.execute(
                    "UPDATE skills SET needs_review=1, status='quarantine' "
                    "WHERE id=?;", (resolved.id,))
            continue
        # "update" / "heal": `resolved` already preserves user-owned state
        mem.l4.upsert(resolved)
        counts["ingested"] += 1
        if resolved.status == "trusted":
            counts["trusted"] += 1
        elif resolved.status == "quarantine":
            counts["quarantine"] += 1
            _queue_for_review(resolved)
        # status 'active' (approved community skill re-verified): indexed,
        # no review queue entry
        if action == "heal":
            logger.warning(
                "Skill '{}' ({}) was restored from its vetted signature (the "
                "stored row had diverged from it) — re-verified trusted.",
                resolved.name, resolved.source)
        # audit log entry (metadata only)
        with st.transaction() as conn:
            conn.execute(
                "INSERT INTO skill_activations(ts,skill_id,agent,turn_id,outcome) "
                "VALUES(?,?,?,?,?);",
                (time.time(), resolved.id, "ingestion", "", "loaded"),
            )
    return counts


def ingest_source(source_dir: Path) -> dict[str, int]:
    """Run the full pipeline on one source directory. Returns counts."""
    paths = discover(source_dir)
    raws = [parse(p) for p in paths]
    skills = [normalize_skill(r) for r in raws]
    for s in skills:
        quarantine_check(s)
    # resolve trigger conflicts within this batch
    skills = resolve_conflicts(skills)
    return index_audit(skills)


def ingest_all_bundled() -> dict[str, int]:
    """Ingest every bundled trusted source under skills_data/. This is what
    runs at first launch (and via /migrate or /skills --reload)."""
    # the corpus lives alongside the package (UNIVERSE_SKILLS_DATA overrides)
    skills_data = skills_data_root()
    total = {"ingested": 0, "trusted": 0, "quarantine": 0, "deduped": 0,
             "deferred_changed": 0, "skipped_tampered": 0}
    for source_name in TRUSTED_SKILL_SOURCES:
        src_dir = skills_data / source_name
        if not src_dir.exists():
            continue
        counts = ingest_source(src_dir)
        for k in total:
            total[k] += counts.get(k, 0)
    return total
