"""v0026 — Universe happy path views: tasks/turns as views over goals.

Keeps goals table intact for compatibility; adds VIEWs so new code can
use task/turn terminology while old queries still work."""
from __future__ import annotations

from schema.migrations import register
from storage.local import LocalStorage


@register(26, "shell happy path views (tasks/turns)")
def upgrade(st: LocalStorage) -> None:
    st.execute("DROP VIEW IF EXISTS tasks;")
    st.execute("DROP VIEW IF EXISTS turns;")
    st.execute("""
        CREATE VIEW tasks AS
        SELECT * FROM goals WHERE kind='task';
    """)
    st.execute("""
        CREATE VIEW turns AS
        SELECT * FROM goals WHERE kind='message';
    """)
    try:
        st.execute("CREATE INDEX IF NOT EXISTS idx_turns_parent ON goals(parent_goal_id) WHERE kind='message';")
    except Exception:
        pass
    try:
        st.execute("ALTER TABLE goals ADD COLUMN last_turn_at REAL;")
    except Exception:
        pass
