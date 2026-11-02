"""core/memory/layers/l1_working.py — Planets & Moons (working memory).

§3, §25 Phase 2 ⑤. The v0 category-reset bug is fixed structurally: category,
domain, and language live in session_context on the Planet object itself, so
complete_task() reads them from there — never re-derived, never defaulted to
"general" at completion time.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from storage.local import get_storage
from core.agent.base_agent import new_id


@dataclass
class Planet:
    """An active main task (working memory)."""
    id: str = ""
    turn_id: str = ""
    goal_text: str = ""
    status: str = "active"  # active | paused | completed | failed | cancelled
    session_context: dict[str, Any] = field(default_factory=dict)
    solar_system_id: str = "default"
    owner_session_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    # convenience accessors that read from session_context — the structural fix
    @property
    def category(self) -> str:
        return str(self.session_context.get("classification", {}).get("category", "general"))

    @property
    def domain(self) -> str:
        return str(self.session_context.get("classification", {}).get("domain", "general"))

    @property
    def language(self) -> str:
        return str(self.session_context.get("language", "en"))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id  # explicit id for persistence
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Planet":
        p = cls(
            id=row["id"], turn_id=row["turn_id"], goal_text=row["goal_text"],
            status=row["status"],
            session_context=json.loads(row.get("session_context") or "{}"),
            solar_system_id=row.get("solar_system_id") or "default",
            owner_session_id=row.get("owner_session_id") or "",
            created_at=row.get("created_at", 0.0),
            updated_at=row.get("updated_at", 0.0),
        )
        return p


@dataclass
class Moon:
    """A sub-task orbiting a Planet."""
    id: str = ""
    planet_id: str = ""
    agent_name: str = ""
    status: str = "pending"
    session_context: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        return d


class L1Working:
    """Planet + Moon CRUD."""

    def __init__(self) -> None:
        self._st = get_storage()

    def create_planet(self, *, turn_id: str, goal_text: str,
                      classification: dict[str, str], language: str = "en",
                      solar_system_id: str = "default",
                      owner_session_id: str = "") -> Planet:
        now = time.time()
        p = Planet(
            id=new_id("planet-"), turn_id=turn_id, goal_text=goal_text,
            status="active",
            session_context={
                "classification": classification,
                "language": language,
            },
            solar_system_id=solar_system_id,
            owner_session_id=owner_session_id,
            created_at=now, updated_at=now,
        )
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT INTO planets(id,goal_text,turn_id,status,created_at,updated_at,"
                "session_context,solar_system_id,owner_session_id) "
                "VALUES(?,?,?,?,?,?,?,?,?);",
                (p.id, p.goal_text, p.turn_id, p.status, p.created_at, p.updated_at,
                 json.dumps(p.session_context),
                 p.solar_system_id, p.owner_session_id),
            )
        return p

    def get_planet(self, planet_id: str) -> Planet | None:
        row = self._st.query_one("SELECT * FROM planets WHERE id=?;", (planet_id,))
        return Planet.from_row(row) if row else None

    def get_planet_by_goal(self, turn_id: str) -> Planet | None:
        row = self._st.query_one("SELECT * FROM planets WHERE turn_id=? ORDER BY created_at DESC LIMIT 1;", (turn_id,))
        return Planet.from_row(row) if row else None

    def update_planet(self, planet: Planet) -> Planet:
        planet.updated_at = time.time()
        with self._st.transaction() as conn:
            conn.execute(
                "UPDATE planets SET goal_text=?, status=?, session_context=?, "
                "solar_system_id=?, updated_at=? WHERE id=?;",
                (planet.goal_text, planet.status, json.dumps(planet.session_context),
                 planet.solar_system_id,
                 planet.updated_at, planet.id),
            )
        return planet

    def list_planets(self, status: str | None = None) -> list[Planet]:
        if status:
            rows = self._st.query_all("SELECT * FROM planets WHERE status=? ORDER BY updated_at DESC;", (status,))
        else:
            rows = self._st.query_all("SELECT * FROM planets ORDER BY updated_at DESC;")
        return [Planet.from_row(r) for r in rows]

    # ---- Moons ------------------------------------------------------------
    def create_moon(self, *, planet_id: str, agent_name: str,
                    session_context: dict[str, Any] | None = None) -> Moon:
        now = time.time()
        m = Moon(id=new_id("moon-"), planet_id=planet_id, agent_name=agent_name,
                 status="pending", session_context=session_context or {},
                 created_at=now, updated_at=now)
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT INTO moons(id,planet_id,agent_name,status,created_at,updated_at,session_context) "
                "VALUES(?,?,?,?,?,?,?);",
                (m.id, m.planet_id, m.agent_name, m.status, m.created_at, m.updated_at,
                 json.dumps(m.session_context)),
            )
        return m

    def list_moons(self, planet_id: str) -> list[Moon]:
        rows = self._st.query_all("SELECT * FROM moons WHERE planet_id=? ORDER BY created_at;", (planet_id,))
        out = []
        for r in rows:
            out.append(Moon(
                id=r["id"], planet_id=r["planet_id"], agent_name=r["agent_name"],
                status=r["status"], session_context=json.loads(r.get("session_context") or "{}"),
                created_at=r["created_at"], updated_at=r["updated_at"],
            ))
        return out

    def update_moon_status(self, moon_id: str, status: str) -> None:
        with self._st.transaction() as conn:
            conn.execute("UPDATE moons SET status=?, updated_at=? WHERE id=?;",
                         (status, time.time(), moon_id))

    def count_active_moons(self, planet_id: str) -> int:
        row = self._st.query_one(
            "SELECT COUNT(*) AS c FROM moons WHERE planet_id=? AND status IN ('pending','running');",
            (planet_id,))
        return int(row["c"]) if row else 0
