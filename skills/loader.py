"""Stubbed — skills disabled (empty catalog per user 2026-10-31)."""
from __future__ import annotations

def _bundled_skill_count() -> int:
    return 0

def load_all_skills() -> dict[str, int]:
    return {"ingested": 0, "trusted": 0, "quarantine": 0, "deduped": 0, "deferred_changed": 0, "skipped_tampered": 0, "taxonomy_skills": 0, "taxonomy_review": 0, "taxonomy_systems": 0, "taxonomy_orbits": 0}

def ensure_skills_bootstrapped() -> dict[str, int | bool]:
    return {"bootstrapped": False, "expected_bundled": 0, "existing_trusted": 0, "quarantined_bundled": 0, "taxonomy_version": "2026.08.1"}

def verify_all_signatures() -> dict[str, int]:
    return {"verified": 0, "mismatch": 0, "missing": 0}

def skill_counts_by_source() -> dict[str, int]:
    return {}

def skills_data_root():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "skills_data"
