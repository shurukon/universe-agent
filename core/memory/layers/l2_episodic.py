"""core/memory/layers/l2_episodic.py — Asteroids + Forgetting Engine.

§3, §25 Phase 2 ⑥. Every task becomes an Asteroid carrying: description,
decisions, obstacles, outcomes, Gravity Score, fingerprint, solar system id,
provenance, and turn_id. The Forgetting Engine compresses / promotes / archives
per the §3 rules. Retention: an asteroid is never deleted within 7 days of
creation regardless of its gravity score (gives the Subconscious Loop time).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from config import ASTEROID_MIN_RETENTION_DAYS
from core.agent.base_agent import new_id
from core.memory.galactic_core import classify_gravity
from storage.local import get_storage


@dataclass
class Asteroid:
    id: str = ""
    turn_id: str = ""
    planet_id: str = ""
    task_description: str = ""
    decisions: list[str] = field(default_factory=list)
    obstacles: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    gravity_score: float = 0.0
    gravity_provenance: dict[str, Any] = field(default_factory=dict)
    fingerprint: dict[str, Any] = field(default_factory=dict)
    fingerprint_hash: str = ""
    solar_system_id: str = "default"
    category: str = "general"
    domain: str = "general"
    language: str = "en"
    task_success: bool = True
    created_at: float = 0.0
    promoted_to: str = ""  # L3 star id once promoted
    owner_session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Asteroid":
        return cls(
            id=row["id"], turn_id=row.get("turn_id") or "",
            planet_id=row.get("planet_id") or "",
            task_description=row["task_description"],
            decisions=json.loads(row.get("decisions") or "[]"),
            obstacles=json.loads(row.get("obstacles") or "[]"),
            outcomes=json.loads(row.get("outcomes") or "[]"),
            gravity_score=float(row.get("gravity_score") or 0.0),
            gravity_provenance=json.loads(row.get("gravity_provenance") or "{}"),
            fingerprint=json.loads(row.get("fingerprint") or "{}"),
            fingerprint_hash=row.get("fingerprint_hash") or "",
            solar_system_id=row.get("solar_system_id") or "default",
            category=row.get("category") or "general",
            domain=row.get("domain") or "general",
            language=row.get("language") or "en",
            task_success=bool(row.get("task_success", 1)),
            created_at=row.get("created_at", 0.0),
            promoted_to=row.get("promoted_to") or "",
            owner_session_id=row.get("owner_session_id") or "",
        )


class L2Episodic:
    def __init__(self) -> None:
        self._st = get_storage()

    def create(self, *, turn_id: str, planet_id: str, task_description: str,
               classification: dict[str, str], language: str = "en",
               decisions: list[str] | None = None, obstacles: list[str] | None = None,
               outcomes: list[str] | None = None, gravity_score: float = 0.0,
               provenance: dict[str, Any] | None = None,
               fingerprint: dict[str, Any] | None = None,
               fingerprint_hash: str = "", solar_system_id: str = "default",
               task_success: bool = True, owner_session_id: str = "") -> Asteroid:
        a = Asteroid(
            id=new_id("asteroid-"), turn_id=turn_id, planet_id=planet_id,
            task_description=task_description,
            decisions=decisions or [], obstacles=obstacles or [], outcomes=outcomes or [],
            gravity_score=gravity_score,
            gravity_provenance=provenance or {},
            fingerprint=fingerprint or {}, fingerprint_hash=fingerprint_hash,
            solar_system_id=solar_system_id,
            category=classification.get("category", "general"),
            domain=classification.get("domain", "general"),
            language=language, task_success=task_success,
            created_at=time.time(), owner_session_id=owner_session_id,
        )
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT INTO asteroids(id,turn_id,planet_id,task_description,decisions,obstacles,"
                "outcomes,gravity_score,gravity_provenance,fingerprint,fingerprint_hash,"
                "solar_system_id,category,domain,language,task_success,created_at,promoted_to,"
                "owner_session_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);",
                (a.id, a.turn_id, a.planet_id, a.task_description,
                 json.dumps(a.decisions), json.dumps(a.obstacles), json.dumps(a.outcomes),
                 a.gravity_score, json.dumps(a.gravity_provenance),
                 json.dumps(a.fingerprint), a.fingerprint_hash, a.solar_system_id,
                 a.category, a.domain, a.language, 1 if a.task_success else 0,
                 a.created_at, a.promoted_to, a.owner_session_id),
            )
        return a

    def get(self, asteroid_id: str) -> Asteroid | None:
        row = self._st.query_one("SELECT * FROM asteroids WHERE id=?;", (asteroid_id,))
        return Asteroid.from_row(row) if row else None

    def update_gravity(self, asteroid_id: str, gravity: float,
                       provenance: dict[str, Any]) -> None:
        with self._st.transaction() as conn:
            conn.execute(
                "UPDATE asteroids SET gravity_score=?, gravity_provenance=? WHERE id=?;",
                (gravity, json.dumps(provenance), asteroid_id),
            )

    def mark_promoted(self, asteroid_id: str, star_id: str) -> None:
        with self._st.transaction() as conn:
            conn.execute("UPDATE asteroids SET promoted_to=? WHERE id=?;",
                         (star_id, asteroid_id))

    def list_for_goal(self, turn_id: str) -> list[Asteroid]:
        rows = self._st.query_all("SELECT * FROM asteroids WHERE turn_id=? ORDER BY created_at;", (turn_id,))
        return [Asteroid.from_row(r) for r in rows]

    def list_unpromoted(self, min_gravity: float = 0.0, limit: int = 100) -> list[Asteroid]:
        rows = self._st.query_all(
            "SELECT * FROM asteroids WHERE promoted_to='' AND gravity_score>=? "
            "ORDER BY gravity_score DESC LIMIT ?;",
            (min_gravity, limit),
        )
        return [Asteroid.from_row(r) for r in rows]

    def list_recent(self, limit: int = 50) -> list[Asteroid]:
        rows = self._st.query_all("SELECT * FROM asteroids ORDER BY created_at DESC LIMIT ?;", (limit,))
        return [Asteroid.from_row(r) for r in rows]

    def delete(self, asteroid_id: str) -> dict[str, Any]:
        """Delete an asteroid, enforcing the 7-day minimum retention (§3).
        Returns a structured result — the old silent None/return meant a
        caller could never tell 'deleted' from 'refused: too recent' from
        'never existed'."""
        a = self.get(asteroid_id)
        if a is None:
            return {"deleted": False, "reason": "not_found"}
        if (time.time() - a.created_at) < ASTEROID_MIN_RETENTION_DAYS * 86400:
            # refuse — too recent
            return {"deleted": False, "reason": "retention_window"}
        with self._st.transaction() as conn:
            conn.execute("DELETE FROM asteroids WHERE id=?;", (asteroid_id,))
        return {"deleted": True, "reason": "ok"}

    # ---- Forgetting Engine (§3) ------------------------------------------
    def forgetting_pass(self) -> dict[str, int]:
        """Apply the §3 retention rules. Returns counts of what happened."""
        now = time.time()
        min_age = ASTEROID_MIN_RETENTION_DAYS * 86400
        stats = {"compressed": 0, "promoted_eligible": 0, "archived": 0, "deleted": 0}
        rows = self._st.query_all("SELECT * FROM asteroids ORDER BY created_at;")
        for r in rows:
            a = Asteroid.from_row(r)
            age = now - a.created_at
            bucket = classify_gravity(a.gravity_score)
            if age < min_age:
                continue  # never within 7 days
            if a.promoted_to:
                # already promoted — old + fully extracted → delete the asteroid
                self._delete_unchecked(a.id)
                stats["deleted"] += 1
            elif bucket == "star_permanent" or bucket == "planet_to_l3":
                stats["promoted_eligible"] += 1  # Subconscious Loop does the actual promotion
            elif bucket == "nebula" and age > 14 * 86400:
                # rare + low value + old → deleted. The counter used to say
                # "archived" while the code hard-deleted — the stat lied
                # (audit 2026-09). There is no archive tier; the honest name
                # is "deleted".
                self._delete_unchecked(a.id)
                stats["deleted"] += 1
            elif age > 30 * 86400 and not a.promoted_to:
                # Old episodic memory is genuinely compressed into an L3 Star.
                # The asteroid is marked as extracted; a later pass may delete it
                # only after the normal retention path sees the promotion.
                from core.memory import get_memory
                mem = get_memory()
                topic = f"memory:{a.id}"
                existing = next((s for s in mem.l3.list_stars(a.domain) if s.topic == topic), None)
                if existing is None:
                    content = json.dumps({
                        "source_asteroid_id": a.id,
                        "turn_id": a.turn_id,
                        "decisions": a.decisions,
                        "obstacles": a.obstacles,
                        "outcomes": a.outcomes,
                        "fingerprint_hash": a.fingerprint_hash,
                    }, ensure_ascii=False, indent=2)
                    existing = mem.l3.create_star(
                        topic=topic, domain=a.domain,
                        summary=(a.decisions[0] if a.decisions else a.task_description[:160]),
                        content=content, owner_session_id=a.owner_session_id,
                    )
                self.mark_promoted(a.id, existing.id)
                stats["compressed"] += 1
        return stats

    def _delete_unchecked(self, asteroid_id: str) -> None:
        with self._st.transaction() as conn:
            conn.execute("DELETE FROM asteroids WHERE id=?;", (asteroid_id,))
