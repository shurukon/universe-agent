"""schema/v0004_add_goals_table.py — top-level goals table + checkpoint refs."""
from schema.migrations import register


@register(4, "add goals table")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            completed_at REAL,
            final_summary TEXT,
            owner_session_id TEXT
        );
    """)
    st.execute("CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);")
