"""universe_desktop_bridge/memory_view.py — the live /api/memory payload.

Serves Universe's memory surfaces (Settings memory browser) from the live
~/.universe database, not from a static export.

Current wiring, honestly stated: the bridge serves the live snapshot at
/api/memory and Settings' memory browser consumes it. The 3D visuals
(trailing orb + welcome orb) are procedural today (buildUniverse with
real=null); the RealUniverseMemory shape stays exported from generate.ts so
a future live wiring drops straight into buildUniverse(quality, real). The
payload shape remains identical to the old static snapshot.

No secrets are exported: ids, names, counts, coordinates only.
"""
from __future__ import annotations

import time
from typing import Any

from storage.local import get_storage

_STAR_LIMIT = 60


def _slim_skill(s: dict[str, Any]) -> dict[str, Any]:
    return {k: s.get(k) for k in (
        "id", "name", "solar_system_id", "orbit_id", "target_agent",
        "confidence", "use_count", "category", "description", "status")}


def memory_snapshot() -> dict[str, Any]:
    """Live systems/orbits/skills/stars + goal planets, all from universe.db."""
    st = get_storage()
    systems = [dict(r) for r in st.query_all(
        "SELECT id, name, description FROM skill_solar_systems ORDER BY name;")]
    orbits = [dict(r) for r in st.query_all(
        "SELECT id, solar_system_id, name, description FROM skill_orbits ORDER BY name;")]
    skills = [dict(r) for r in st.query_all(
        "SELECT id, name, solar_system_id, orbit_id, target_agent, confidence, "
        "use_count, category, description, status FROM skills;")]
    activations = {r["skill_id"]: r["n"] for r in st.query_all(
        "SELECT skill_id, COUNT(*) AS n FROM skill_activations GROUP BY skill_id;")}

    by_system: dict[str, list[dict]] = {}
    by_orbit: dict[str, list[dict]] = {}
    for s in skills:
        by_system.setdefault(s["solar_system_id"], []).append(s)
        by_orbit.setdefault(s["orbit_id"], []).append(s)

    for sysrow in systems:
        sid = sysrow["id"]
        sys_skills = by_system.get(sid, [])
        sysrow["skill_count"] = len(sys_skills)
        sysrow["orbits"] = [
            {**o, "skill_count": len(by_orbit.get(o["id"], []))}
            for o in orbits if o["solar_system_id"] == sid
        ]

    # top-confidence skills become the L3 stars (dedup by name, keep best)
    best: dict[str, dict[str, Any]] = {}
    for s in skills:
        cur = best.get(s["name"])
        if cur is None or (s["confidence"] or 0) > (cur["confidence"] or 0):
            best[s["name"]] = s
    stars = sorted(best.values(),
                   key=lambda s: -(s["confidence"] or 0))[:_STAR_LIMIT]
    for i, s in enumerate(stars):
        s["rank"] = i

    # L1 planets = real goals the sidebar/universe can show
    planets = [dict(r) for r in st.query_all(
        "SELECT id, turn_id, goal_text, status, created_at, updated_at "
        "FROM planets ORDER BY created_at DESC LIMIT 200;")]

    return {
        "generated_at": round(time.time(), 3),
        "counts": {
            "skills": len(skills),
            "systems": len(systems),
            "orbits": len(orbits),
            "trusted": sum(1 for s in skills if s["status"] == "trusted"),
        },
        "systems": systems,
        "stars": stars,
        "skillsByOrbit": {oid: [_slim_skill(s) for s in group]
                          for oid, group in by_orbit.items()},
        "activation_counts": activations,
        "goals": planets,
    }
