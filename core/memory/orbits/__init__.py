"""core/memory/orbits/__init__.py — Local + Galactic orbits (user model).

§3, §25 Phase 2 ⑫. The v0 duplicate-orbit-rows bug is fixed structurally:
to_dict() includes an explicit "id" field, and save() matches by id rather
than generating a new UUID on every save.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any

from storage.local import get_storage
from core.agent.base_agent import new_id


@dataclass
class GalacticOrbit:
    """General user profile accumulated across all Local Orbits (§3)."""
    id: str = ""
    name: str = ""
    profession: str = ""
    goals: list[str] = field(default_factory=list)
    communication_style: str = "concise"
    preferred_language: str = "en"
    skill_level_general: str = "intermediate"
    control_preference: str = "guided"  # guided | autonomous
    recurring_mistakes: list[str] = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # explicit id — the structural fix for v0's duplicate-rows bug
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GalacticOrbit":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass
class LocalOrbit:
    """Per-solar-system user model (skill level, preferences, mistakes)."""
    id: str = ""
    solar_system_id: str = ""
    skill_level: str = "intermediate"
    preferred_style: str = "standard"
    control_preference: str = "guided"
    recurring_mistakes: list[str] = field(default_factory=list)
    preferences: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LocalOrbit":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


class OrbitsStore:
    """CRUD for both orbit kinds. Single shared instance."""

    def __init__(self) -> None:
        self._st = get_storage()

    # ---- Galactic ---------------------------------------------------------
    def get_galactic(self) -> GalacticOrbit:
        row = self._st.query_one("SELECT * FROM orbits WHERE kind='galactic' LIMIT 1;")
        if row:
            data = dict(row)
            data.pop("kind", None)
            data.pop("solar_system_id", None)
            import json
            merged = GalacticOrbit().__dict__
            merged.update(json.loads(row["data"]))
            merged["id"] = row["id"]
            return GalacticOrbit.from_dict(merged)
        return GalacticOrbit()

    def save_galactic(self, orbit: GalacticOrbit) -> GalacticOrbit:
        import json
        if not orbit.id:
            orbit.id = new_id("orbit-galactic-")
        orbit.updated_at = time.time()
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO orbits(id, kind, solar_system_id, data, updated_at) "
                "VALUES(?,?,?,?,?);",
                (orbit.id, "galactic", None, json.dumps(orbit.to_dict()), orbit.updated_at),
            )
        return orbit

    # ---- Local ------------------------------------------------------------
    def get_local(self, solar_system_id: str) -> LocalOrbit:
        row = self._st.query_one(
            "SELECT * FROM orbits WHERE kind='local' AND solar_system_id=? LIMIT 1;",
            (solar_system_id,),
        )
        if row:
            import json
            merged = LocalOrbit().__dict__
            merged.update(json.loads(row["data"]))
            merged["id"] = row["id"]
            merged["solar_system_id"] = solar_system_id
            return LocalOrbit.from_dict(merged)
        loc = LocalOrbit(solar_system_id=solar_system_id)
        return loc

    def save_local(self, orbit: LocalOrbit) -> LocalOrbit:
        import json
        if not orbit.id:
            orbit.id = new_id("orbit-local-")
        if not orbit.solar_system_id:
            orbit.solar_system_id = "default"
        orbit.updated_at = time.time()
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO orbits(id, kind, solar_system_id, data, updated_at) "
                "VALUES(?,?,?,?,?);",
                (orbit.id, "local", orbit.solar_system_id,
                 json.dumps(orbit.to_dict()), orbit.updated_at),
            )
        return orbit

    def list_local(self) -> list[LocalOrbit]:
        import json
        rows = self._st.query_all("SELECT * FROM orbits WHERE kind='local' ORDER BY updated_at DESC;")
        out: list[LocalOrbit] = []
        for r in rows:
            d = json.loads(r["data"])
            d["id"] = r["id"]
            out.append(LocalOrbit.from_dict(d))
        return out


_orbits: OrbitsStore | None = None


def get_orbits() -> OrbitsStore:
    global _orbits
    if _orbits is None:
        _orbits = OrbitsStore()
    return _orbits
