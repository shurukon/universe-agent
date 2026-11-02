"""v0028 — single universe purge: drop orphan agent routes + empty skills corpus (no mocks)."""
from __future__ import annotations
from schema.migrations import register
from storage.local import LocalStorage

@register(28, "single universe: purge 5 agents + 1584 skills orphan rows (honest empty)")
def upgrade(st: LocalStorage) -> None:
    for table, col in (("agent_models", "agent"), ("agent_fallbacks", "agent"), ("agent_provider_keys", "agent")):
        try:
            st.execute(f"DELETE FROM {table} WHERE {col} IN ('research','code','review','security','design');")
        except Exception:
            pass
    for tbl in ("skill_taxonomy_audit", "skill_agent_ownership", "skill_orbits", "skill_solar_systems", "skill_quarantine", "skill_activations", "skills"):
        try:
            st.execute(f"DELETE FROM {tbl};")
        except Exception:
            pass
    def _cols(t: str) -> set[str]:
        try:
            return {r["name"] for r in st.query_all(f"PRAGMA table_info({t});")}
        except Exception:
            return set()
    scols = _cols("sessions")
    for col, ddl in (
        ("project_id", "ALTER TABLE sessions ADD COLUMN project_id TEXT DEFAULT '';"),
        ("archived", "ALTER TABLE sessions ADD COLUMN archived INTEGER DEFAULT 0;"),
        ("deleted", "ALTER TABLE sessions ADD COLUMN deleted INTEGER DEFAULT 0;"),
        ("pinned", "ALTER TABLE sessions ADD COLUMN pinned INTEGER DEFAULT 0;"),
    ):
        if col not in scols:
            try:
                st.execute(ddl)
            except Exception:
                pass
