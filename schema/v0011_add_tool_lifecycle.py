"""Persist capability catalog entries and per-goal tool lifecycle evidence."""
from schema.migrations import register


@register(11, "add capability catalog and tool lifecycle evidence")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS tool_catalog (
            name TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'runtime',
            status TEXT NOT NULL DEFAULT 'registered',
            details TEXT NOT NULL DEFAULT '{}',
            updated_at REAL NOT NULL
        );
    """)
    st.execute("""
        CREATE TABLE IF NOT EXISTS tool_lifecycle (
            id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL DEFAULT '',
            agent TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            phase TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'runtime',
            details TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
    """)
    st.execute("CREATE INDEX IF NOT EXISTS idx_tool_lifecycle_goal ON tool_lifecycle(goal_id, created_at);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_tool_lifecycle_name ON tool_lifecycle(name, created_at);")


def downgrade(st) -> None:
    st.execute("DROP TABLE IF EXISTS tool_lifecycle;")
    st.execute("DROP TABLE IF EXISTS tool_catalog;")
