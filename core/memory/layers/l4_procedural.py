"""core/memory/layers/l4_procedural.py — Skills + lifecycle.

§3, §5, §18, §25 Phase 2 ⑩. Pre-loaded trusted skills land here at confidence
0.90–0.95 via the ingestion pipeline (skill/ingestion.py). Skill lifecycle
(Nebula → Protostar → Full Star → White Dwarf → Black Hole) is given concrete
transition rules by the confidence-decay model in §18:
  - start at 0.90–0.95 (trusted) or lower (community)
  - decay after 90 days idle → drop to 0.70 (still active, ranked lower)
  - bump back to 0.95 after a successful activation
  - drop sharply to 0.50 with needs_review after a failure
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from config import SKILL_CONFIDENCE
from storage.local import get_storage
from core.agent.base_agent import new_id

# L4 skill-search ranking boosts (§5) — named so the ranking formula has one
# home; agent-owned skills outrank, taxonomy confidence nudges.
L4_SEARCH_BOOSTS = {
    "agent_owned": 2.0,     # a skill explicitly assigned to this agent
    "taxonomy_confidence": 0.1,  # cap on the confidence nudge (×confidence 0..1)
}


@dataclass
class Skill:
    id: str = ""
    name: str = ""
    source: str = ""
    version: str = "1.0.0"
    description: str = ""
    body: str = ""
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    license: str = "MIT"
    confidence: float = 0.9
    status: str = "trusted"  # trusted | quarantine | active | archived
    signature: str = ""
    category: str = "general"
    target_agent: str = ""
    last_used: float = 0.0
    last_verified: float = 0.0
    use_count: int = 0
    needs_review: bool = False
    solar_system_id: str = ""
    orbit_id: str = ""
    taxonomy_version: str = ""
    taxonomy_confidence: float = 0.0
    taxonomy_reason: str = ""
    taxonomy_needs_review: bool = False
    created_at: float = 0.0
    # v20 — the Settings→Skills toggle: an off skill stays stored (durable
    # corpus, signatures intact) but never reaches an agent's L4 search
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Skill":
        return cls(
            id=row["id"], name=row["name"], source=row["source"],
            version=row.get("version") or "1.0.0", description=row.get("description") or "",
            body=row.get("body") or "",
            tags=json.loads(row.get("tags") or "[]"),
            triggers=json.loads(row.get("triggers") or "[]"),
            license=row.get("license") or "MIT",
            confidence=float(row.get("confidence") or 0.9),
            status=row.get("status") or "trusted",
            signature=row.get("signature") or "",
            category=row.get("category") or "general",
            target_agent=row.get("target_agent") or "",
            last_used=float(row.get("last_used") or 0.0),
            last_verified=float(row.get("last_verified") or 0.0),
            use_count=int(row.get("use_count") or 0),
            needs_review=bool(row.get("needs_review", 0)),
            solar_system_id=row.get("solar_system_id") or "",
            orbit_id=row.get("orbit_id") or "",
            taxonomy_version=row.get("taxonomy_version") or "",
            taxonomy_confidence=float(row.get("taxonomy_confidence") or 0.0),
            taxonomy_reason=row.get("taxonomy_reason") or "",
            taxonomy_needs_review=bool(row.get("taxonomy_needs_review", 0)),
            created_at=float(row.get("created_at") or 0.0),
            enabled=bool(row.get("enabled", 1)),
        )


class L4Procedural:
    def __init__(self) -> None:
        self._st = get_storage()
        # BM25 search cache (engine-fix round): search() re-listed ALL
        # trusted skills and re-tokenized their bodies on EVERY call. The
        # cache is keyed on the exact search-window filters plus a
        # corpus_version that every write bumps — O(1) invalidation, no
        # row diffing, and a stale window can never match.
        self._corpus_version = 0
        self._search_cache: tuple[tuple, Any, list[Skill]] | None = None

    def _invalidate(self) -> None:
        self._corpus_version += 1
        self._search_cache = None

    def upsert(self, skill: Skill) -> Skill:
        if not skill.id:
            skill.id = new_id("skill-")
        if not skill.created_at:
            skill.created_at = time.time()
        if not skill.last_verified:
            skill.last_verified = time.time()
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO skills(id,name,source,version,description,body,tags,"
                "triggers,license,confidence,status,signature,category,target_agent,last_used,"
                "last_verified,use_count,needs_review,solar_system_id,orbit_id,taxonomy_version,"
                "taxonomy_confidence,taxonomy_reason,taxonomy_needs_review,created_at,enabled) "
                "VALUES(" + ",".join(["?"] * 26) + ");",
                (skill.id, skill.name, skill.source, skill.version, skill.description,
                 skill.body, json.dumps(skill.tags), json.dumps(skill.triggers),
                 skill.license, skill.confidence, skill.status, skill.signature,
                 skill.category, skill.target_agent, skill.last_used, skill.last_verified,
                 skill.use_count, 1 if skill.needs_review else 0, skill.solar_system_id,
                 skill.orbit_id, skill.taxonomy_version, skill.taxonomy_confidence,
                 skill.taxonomy_reason, 1 if skill.taxonomy_needs_review else 0, skill.created_at,
                 1 if skill.enabled else 0),
            )
        self._invalidate()
        return skill

    def get(self, skill_id: str) -> Skill | None:
        row = self._st.query_one("SELECT * FROM skills WHERE id=?;", (skill_id,))
        return Skill.from_row(row) if row else None

    def find_by_name(self, name: str, source: str | None = None) -> Skill | None:
        if source:
            row = self._st.query_one("SELECT * FROM skills WHERE name=? AND source=?;", (name, source))
        else:
            row = self._st.query_one("SELECT * FROM skills WHERE name=? ORDER BY confidence DESC LIMIT 1;", (name,))
        return Skill.from_row(row) if row else None

    def list(self, status: str | None = None, target_agent: str | None = None,
             category: str | None = None) -> list[Skill]:
        q = "SELECT * FROM skills"
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if target_agent:
            clauses.append("target_agent=?")
            params.append(target_agent)
        if category:
            clauses.append("category=?")
            params.append(category)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY confidence DESC, use_count DESC;"
        rows = self._st.query_all(q, tuple(params))
        return [Skill.from_row(r) for r in rows]

    # ---- BM25 search over name + description + tags ----------------------
    def search(self, query: str, category: str = "", domain: str = "",
               target_agent: str = "", solar_system: str = "", orbit: str = "",
               top_k: int = 5) -> list[Skill]:
        from rank_bm25 import BM25Okapi
        # Unicode-aware tokenizer (m3): matches L3's — non-Latin skill names
        # and queries were invisible to BM25 under the old `[a-z0-9_]+`
        from core.memory.layers.l3_semantic import _tokenize as _tok_any
        # v20: a skill the user switched off never reaches an agent —
        # it stays in the corpus but is invisible to every L4 search
        cache_key = (target_agent, category, solar_system, orbit,
                     self._corpus_version)
        if self._search_cache is not None and self._search_cache[0] == cache_key:
            skills = self._search_cache[2]
            bm25 = self._search_cache[1]
        else:
            skills = [s for s in self.list(status="trusted") if s.enabled]
            # Agent ownership is a routing preference, not a reason to hide shared
            # skills. An explicitly assigned skill or an unassigned cross-agent skill
            # remains eligible for that agent.
            if target_agent:
                # S1: the caller uses the new 6-agent registry; stored skill rows
                # may still carry retired names (pre-migration or re-ingested
                # bundled data). Accept the heir's own rows AND the legacy names
                # that alias to it.
                from skill.taxonomy import LEGACY_AGENT_ALIASES
                legacy_names = {old for old, new in LEGACY_AGENT_ALIASES.items()
                                if new == target_agent}
                skills = [s for s in skills
                          if not s.target_agent or s.target_agent == target_agent
                          or s.target_agent in legacy_names]
            aliases = {
                "web_development": {"software_engineering", "product_design", "integration_protocols", "browser_automation"},
                "ui_ux_design": {"product_design", "software_engineering"},
                "api_integration": {"integration_protocols", "software_engineering", "security_trust"},
                "web_automation": {"browser_automation", "integration_protocols"},
                "code_generation": {"software_engineering"},
                "data_analysis": {"data_intelligence", "software_engineering"},
                "devops": {"platform_operations", "security_trust"},
            }
            if category:
                systems = aliases.get(category)
                if systems:
                    skills = [s for s in skills if s.solar_system_id in {f"skill-system-{x}" for x in systems} or s.category == category]
                else:
                    skills = [s for s in skills if s.category == category or f"category:{category}" in s.tags]
            if solar_system:
                skills = [s for s in skills if s.solar_system_id in {solar_system, f"skill-system-{solar_system}"}]
            if orbit:
                skills = [s for s in skills if s.orbit_id.endswith(f"-orbit-{orbit}") or s.orbit_id == orbit]
            if not skills:
                return []
            corpus = [_tok_any(f"{s.name} {s.description} {' '.join(s.tags)} {' '.join(s.triggers)} {s.category} {s.solar_system_id} {s.orbit_id}") for s in skills]
            bm25 = BM25Okapi(corpus)
            self._search_cache = (cache_key, bm25, skills)
        scores = bm25.get_scores(_tok_any(f"{query} {domain}"))
        ranked = []
        for score, skill in zip(scores, skills):
            if target_agent and skill.target_agent == target_agent:
                score += L4_SEARCH_BOOSTS["agent_owned"]
            if skill.taxonomy_confidence:
                score += (min(skill.taxonomy_confidence, 1.0)
                          * L4_SEARCH_BOOSTS["taxonomy_confidence"])
            ranked.append((score, skill))
        ranked.sort(key=lambda t: t[0], reverse=True)
        return [skill for score, skill in ranked[:top_k] if score > 0]

    # ---- lifecycle (§18) -------------------------------------------------
    def record_activation(self, skill_id: str, agent: str, turn_id: str,
                          outcome: str) -> None:
        """outcome: success | failure. Drives confidence decay/bump (§18)."""
        now = time.time()
        skill = self.get(skill_id)
        if not skill:
            return
        if outcome == "success":
            skill.confidence = SKILL_CONFIDENCE["success_bump_to"]
            skill.needs_review = False
        else:
            skill.confidence = SKILL_CONFIDENCE["failure_drop_to"]
            skill.needs_review = True
        skill.use_count += 1
        skill.last_used = now
        self.upsert(skill)
        with self._st.transaction() as conn:
            conn.execute(
                "INSERT INTO skill_activations(ts,skill_id,agent,turn_id,outcome) VALUES(?,?,?,?,?);",
                (now, skill_id, agent, turn_id, outcome),
            )

    def apply_idle_decay(self) -> int:
        """Drop confidence on skills idle > 90 days (§18). Returns count decayed."""
        cutoff = time.time() - SKILL_CONFIDENCE["idle_decay_days"] * 86400
        rows = self._st.query_all(
            "SELECT * FROM skills WHERE last_used < ? AND confidence > ?;",
            (cutoff, SKILL_CONFIDENCE["idle_decay_to"]),
        )
        for r in rows:
            with self._st.transaction() as conn:
                conn.execute("UPDATE skills SET confidence=? WHERE id=?;",
                             (SKILL_CONFIDENCE["idle_decay_to"], r["id"]))
        if rows:
            self._invalidate()  # confidence changed → ranking may change
        return len(rows)

    def pin_version(self, name: str, source: str, version: str) -> bool:
        row = self._st.query_one(
            "SELECT id, body FROM skills WHERE name=? AND source=?;", (name, source))
        if not row:
            return False
        # version is PART of the signature payload (skill.signing.sign_skill):
        # pinning used to update the version column alone, so the weekly
        # verify then failed the signature and AUTO-QUARANTINED the skill
        # the user had just deliberately pinned. Re-sign over the kept body
        # with the new version (audit 2026-10, deferred from the skills
        # cluster's report).
        from skill.signing import sign_skill
        signature = sign_skill(name, source, version, row["body"] or "")
        with self._st.transaction() as conn:
            conn.execute("UPDATE skills SET version=?, signature=? WHERE id=?;",
                         (version, signature, row["id"]))
        self._invalidate()
        return True
