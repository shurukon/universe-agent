"""v0030 — turn identity: rename goal_id -> turn_id in runtime tables (session authority)."""
from __future__ import annotations
from schema.migrations import register
from storage.local import LocalStorage

@register(30, "turn identity: goal_id -> turn_id (planets, asteroids, etc.)")
def upgrade(st: LocalStorage) -> None:
    def _has_col(table: str, col: str) -> bool:
        try:
            return any(r["name"] == col for r in st.query_all(f"PRAGMA table_info({table});"))
        except Exception:
            return False
    def _rename(table: str, old: str, new: str):
        if _has_col(table, old) and not _has_col(table, new):
            try:
                st.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new};")
            except Exception:
                pass
    for tbl in ("planets", "asteroids", "handoffs", "task_outcomes", "skill_activations", "capability_grants", "tool_lifecycle", "goal_feedback"):
        _rename(tbl, "goal_id", "turn_id")
    try:
        if _has_col("goal_feedback", "goal_id"):
            st.execute("ALTER TABLE goal_feedback RENAME TO turn_feedback;")
    except Exception:
        pass
    try:
        st.execute("DROP VIEW IF EXISTS goal_feedback;")
        st.execute("CREATE VIEW IF NOT EXISTS goal_feedback AS SELECT * FROM turn_feedback;")
    except Exception:
        pass
