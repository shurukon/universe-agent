"""UI overhaul v14: projects, chat lifecycle (archive/delete/branch/pin),
MCP server transports (sse/http), and onboarding columns on goals."""
from schema.migrations import register


@register(14, "projects + goal lifecycle + mcp transports")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            root TEXT NOT NULL DEFAULT '',
            created_at REAL,
            updated_at REAL,
            archived INTEGER NOT NULL DEFAULT 0
        );
    """)
    # default project so existing goals have a home in the sidebar
    st.execute(
        "INSERT OR IGNORE INTO projects(id, name, root, created_at, updated_at) "
        "VALUES('general', 'General', '', strftime('%s','now'), strftime('%s','now'));"
    )
    for col, ddl in (
        ("project_id", "TEXT DEFAULT NULL"),
        ("archived", "INTEGER NOT NULL DEFAULT 0"),
        ("deleted", "INTEGER NOT NULL DEFAULT 0"),
        ("title", "TEXT DEFAULT NULL"),
        ("parent_goal_id", "TEXT DEFAULT NULL"),
        ("pinned", "INTEGER NOT NULL DEFAULT 0"),
    ):
        st.execute(f"ALTER TABLE goals ADD COLUMN {col} {ddl};")
    for col, ddl in (
        ("transport", "TEXT NOT NULL DEFAULT 'stdio'"),
        ("url", "TEXT NOT NULL DEFAULT ''"),
        ("headers", "TEXT NOT NULL DEFAULT '{}'"),
        ("enabled", "INTEGER NOT NULL DEFAULT 1"),
    ):
        st.execute(f"ALTER TABLE mcp_servers ADD COLUMN {col} {ddl};")
