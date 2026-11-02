"""skill/conflict.py — dedup + conflict resolution (§18).

Dedup: group by (name, source); keep the highest version within a group, the
highest confidence across sources.
Conflict: when two skills claim the same trigger, the higher-confidence one
wins; a genuine tie is resolved by asking the user once (here, we log the tie
and rank the loser lower). The losing skill stays indexed, just ranked lower.
"""
from __future__ import annotations

from collections import defaultdict

from core.memory.layers.l4_procedural import Skill


def _semver_key(v: str) -> tuple[int, int, int]:
    try:
        parts = v.split(".")
        return tuple(int(p) for p in parts[:3])  # type: ignore
    except Exception:
        return (0, 0, 0)


def dedup_skills(skills: list[Skill]) -> list[Skill]:
    """Group by (name, source); keep the highest version in each group."""
    by_key: dict[tuple[str, str], Skill] = {}
    for s in skills:
        key = (s.name, s.source)
        if key not in by_key:
            by_key[key] = s
        else:
            if _semver_key(s.version) > _semver_key(by_key[key].version):
                by_key[key] = s
    return list(by_key.values())


def resolve_conflicts(skills: list[Skill]) -> list[Skill]:
    """When multiple skills share a trigger, the higher-confidence wins (kept
    as-is); losers get a small confidence penalty so they rank lower in
    retrieval. Genuine ties (same confidence) are logged via the audit trail.
    Trusted-source skills never drop below 0.90 (§5 floor)."""
    from config import TRUSTED_SKILL_SOURCES
    trigger_map: dict[str, list[Skill]] = defaultdict(list)
    for s in skills:
        for t in s.triggers:
            trigger_map[t].append(s)
    for trigger, group in trigger_map.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda s: s.confidence, reverse=True)
        winner = group[0]
        # check for genuine tie
        tied = [s for s in group[1:] if s.confidence == winner.confidence]
        if tied:
            # log the tie — the user would be asked once in the interactive UI
            _log_conflict(trigger, group)
        # penalize losers slightly (still indexed, ranked lower)
        for loser in group[1:]:
            floor = 0.90 if loser.source in TRUSTED_SKILL_SOURCES else 0.40
            loser.confidence = max(floor, loser.confidence - 0.05)
    return skills


def _log_conflict(trigger: str, group: list[Skill]) -> None:
    try:
        from storage.local import get_storage
        import time
        st = get_storage()
        with st.transaction() as conn:
            conn.execute(
                "INSERT INTO skill_activations(ts,skill_id,agent,turn_id,outcome) "
                "VALUES(?,?,?,?,?);",
                (time.time(), f"conflict:{trigger}", "ingestion", "",
                 "tie:" + ",".join(f"{s.name}@{s.source}" for s in group)),
            )
    except Exception:
        pass
