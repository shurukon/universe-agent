"""core/memory/__init__.py — UniverseMemory facade.

§25 Phase 2 ⑬. The unified memory interface every subsystem talks to. The v0
complete_task bug (category reset to "general" at completion time) is fixed
structurally: complete_task reads category/domain/language from the Planet's
session_context, never re-derives them.

The facade wires L1–L5, orbits, rules (Black/White/Worm holes), and the
Cosmic Core (gravity). It exposes the small, well-typed surface the
Orchestrator and agents actually need.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from storage.local import get_storage
from core.agent.base_agent import new_id
from core.memory.layers.l1_working import L1Working, Planet
from core.memory.layers.l2_episodic import L2Episodic, Asteroid
from core.memory.layers.l3_semantic import L3Semantic, Star
from core.memory.layers.l4_procedural import L4Procedural, Skill
from core.memory.layers.l5_meta import L5Meta
from core.memory.orbits import get_orbits
from core.memory.galactic_core import compute_gravity, classify_gravity, should_promote
from config import SUBCONSCIOUS_PROMOTE_AT


class UniverseMemory:
    """The single memory surface. Holds the layer stores and orchestrates
    cross-layer flows (complete_task, promotion, recall)."""

    def __init__(self) -> None:
        self.l1 = L1Working()
        self.l2 = L2Episodic()
        self.l3 = L3Semantic()
        self.l4 = L4Procedural()
        self.l5 = L5Meta()
        self.orbits = get_orbits()
        self._st = get_storage()

    # ---- search surfaces (Research-First Protocol) -----------------------
    def search_l4(self, query: str, category: str = "", domain: str = "",
                  target_agent: str = "", solar_system: str = "", orbit: str = "",
                  top_k: int = 5) -> list[Skill]:
        return self.l4.search(query, category=category, domain=domain,
                              target_agent=target_agent, solar_system=solar_system,
                              orbit=orbit, top_k=top_k)

    def search_l3(self, query: str, top_k: int = 5) -> list[Star]:
        return self.l3.search(query, top_k=top_k)

    def active_rules(self) -> list[dict[str, Any]]:
        rows = self._st.query_all("SELECT * FROM rules ORDER BY created_at DESC;")
        return [{"id": r["id"], "kind": r["kind"], "rule": r["rule"],
                 "scope": r["scope"]} for r in rows]

    # ---- rules (Black/White/Worm holes) ---------------------------------
    def add_rule(self, *, kind: str, rule: str, scope: str = "global",
                 created_by: str = "user") -> dict[str, Any]:
        rid = new_id("rule-")
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT INTO rules(id,kind,rule,scope,created_at,created_by) VALUES(?,?,?,?,?,?);",
                (rid, kind, rule, scope, time.time(), created_by),
            )
        return {"id": rid, "kind": kind, "rule": rule, "scope": scope}

    def remove_rule(self, rule_id: str) -> bool:
        with self._st.transaction() as conn:
            cur = conn.execute("DELETE FROM rules WHERE id=?;", (rule_id,))
            return cur.rowcount > 0

    def blackholes(self) -> list[dict[str, Any]]:
        return [r for r in self.active_rules() if r["kind"] == "blackhole"]

    def is_blackhooled(self, action: str) -> bool:
        # a black hole rule blocks any action whose description contains the rule text
        for r in self.blackholes():
            if r["rule"] and r["rule"].lower() in action.lower():
                return True
        return False

    # ---- the critical complete_task flow (§3, v0 bug fixed) -------------
    def complete_task(self, *, planet: Planet, handoffs: list[dict[str, Any]],
                      classification: dict[str, str] | None = None,
                      language: str | None = None) -> Asteroid:
        """Final, atomic end-of-goal analysis. Reads category/domain/language
        from the Planet's session_context — never re-derives, never defaults
        to 'general'. Computes gravity, records the asteroid, promotes if
        eligible, records the L5 outcome, marks the Planet complete."""
        # THE fix: read from session_context on the planet itself (§3)
        cls = classification or planet.session_context.get("classification", {})
        lang = language or planet.session_context.get("language", "en")
        category = cls.get("category", planet.category or "general")
        domain = cls.get("domain", planet.domain or "general")

        gravity, prov = compute_gravity(handoffs)
        # fingerprint: the repeat hash is (category, intent, goal-text digest)
        # ONLY — computed identically here and at the orchestrator's repeat
        # lookup. It used to hash the real tools_used union while the lookup
        # hashed [], so the two sides could never collide and exact-repeat
        # recall was dead for any goal that touched a tool (audit 2026-09).
        # The tool set stays in the fingerprint record as evidence.
        tool_set = sorted({t for h in handoffs for t in (h.get("tools_used") or [])})
        from core.memory.fingerprint import build_fingerprint
        fp = build_fingerprint(cls, [], planet.goal_text)
        fp.tool_set = tool_set

        # Success verdict: the FINAL handoff carries it, not a blind all().
        # _apply_acceptance already coerces the final step to failed when
        # acceptance did not pass, and the orchestrator's recovery ladder
        # re-runs failed phases — but a stray mid-goal failure (one failed
        # file.read inside a completed build) must not launder a finished,
        # accepted goal into failure. Empty handoffs = no evidence = failure.
        # Acceptance override (live round 4): when the FINAL step failed on
        # model prose (a free model returning <tool_call> XML instead of the
        # handoff JSON) while the acceptance contract verified real tool
        # evidence, the contract wins the goal verdict. The step record
        # itself stays truthfully failed (honesty rule preserved).
        final = handoffs[-1] if handoffs else {}
        if handoffs:
            success = bool(final.get("task_success", False))
            if not success:
                ctx = final.get("context_for_memory") or {}
                # Acceptance override (pinned by test_acceptance_verdict):
                # the acceptance contract may carry the TURN verdict when at
                # least one step genuinely succeeded — a final handoff that
                # died on model prose must not veto proven work. It may NOT
                # rescue an all-failed goal: acceptance_success with zero
                # successful steps stays a failure (audit 2026-09 kept this
                # requirement deliberately — an acceptance probe passing
                # while every step failed is evidence of a wrong artifact,
                # not success). The step record itself stays failed either
                # way; the honesty rule is preserved in the per-step history.
                success = (bool(ctx.get("acceptance_success"))
                           and any(h.get("task_success") for h in handoffs))
        else:
            success = False
        decisions = list({d for h in handoffs for d in (h.get("key_decisions") or [])})
        obstacles = []
        for h in handoffs:
            if not h.get("task_success", True):
                obstacles.append(h.get("what_was_done", ""))
        outcomes = [h.get("what_was_done", "") for h in handoffs]

        asteroid = self.l2.create(
            turn_id=planet.turn_id, planet_id=planet.id,
            task_description=planet.goal_text,
            classification={"category": category, "domain": domain, "language": lang},
            language=lang, decisions=decisions, obstacles=obstacles, outcomes=outcomes,
            gravity_score=gravity, provenance=prov.to_dict(),
            fingerprint=fp.to_dict(), fingerprint_hash=fp.repeat_hash,
            solar_system_id=planet.solar_system_id, task_success=success,
            owner_session_id=planet.owner_session_id,
        )
        try:
            from observability.events import emit as _emit
            _emit("memory_updated", turn_id=planet.turn_id, layer="L2",
                  kind="asteroid", gravity_score=round(gravity, 3),
                  success=success)
        except Exception:
            pass
        # L5 outcome (windowed error_rate)
        self.l5.record_outcome(domain=domain, success=success, turn_id=planet.turn_id)
        # promote?
        classify_gravity(gravity)
        if should_promote(gravity):
            star = self.l3.create_star(
                topic=planet.goal_text[:80],
                domain=domain,
                summary=decisions[0] if decisions else asteroid.task_description[:120],
                content="\n".join(outcomes)[:2000],
                owner_session_id=planet.owner_session_id,
            )
            self.l2.mark_promoted(asteroid.id, star.id)
            asteroid.promoted_to = star.id  # keep the in-memory object consistent
            try:
                from observability.events import emit as _emit
                _emit("memory_updated", turn_id=planet.turn_id, layer="L3",
                      kind="star_born", topic=star.topic[:80], domain=domain)
            except Exception:
                pass
        # mark planet complete
        planet.status = "completed" if success else "failed"
        self.l1.update_planet(planet)
        # L5 mirror refresh
        self.l5.refresh_mirror(self)
        return asteroid

    # ---- Subconscious-Loop-driven promotion (idle-time, §3) -------------
    def promote_idle(self, threshold: float = SUBCONSCIOUS_PROMOTE_AT) -> list[str]:
        """Promote asteroids with gravity >= threshold that haven't been
        promoted yet. Returns the list of promoted asteroid ids."""
        promoted: list[str] = []
        for a in self.l2.list_unpromoted(min_gravity=threshold):
            star = self.l3.create_star(
                topic=a.task_description[:80], domain=a.domain,
                summary=f"Promoted from L2 (gravity={a.gravity_score:.2f})",
                content=str(a.outcomes)[:2000],
                owner_session_id=a.owner_session_id,
            )
            self.l2.mark_promoted(a.id, star.id)
            promoted.append(a.id)
        return promoted

    def vault_sync(self) -> dict[str, int]:
        return self.l3.sync_from_vault()


_memory: UniverseMemory | None = None
_memory_lock = threading.RLock()


def get_memory() -> UniverseMemory:
    """Double-checked-locking singleton (engine-fix round): two threads
    racing get_memory() on first use each built a UniverseMemory — the
    loser's layer stores were silently dropped, and any rows written
    through them diverged from the singleton's storage handles. RLock
    because the bootstrap below re-enters get_memory()."""
    global _memory
    if _memory is None:
        with _memory_lock:
            if _memory is None:
                _memory = UniverseMemory()
                # Bundled skills are durable package data; the home DB is a materialized
                # index. Bootstrap after publishing the singleton to break loader cycles.
                from skills.loader import ensure_skills_bootstrapped
                ensure_skills_bootstrapped()
    return _memory


def reset_memory_for_tests() -> UniverseMemory:
    global _memory
    _memory = UniverseMemory()
    from skills.loader import ensure_skills_bootstrapped
    ensure_skills_bootstrapped()
    return _memory
