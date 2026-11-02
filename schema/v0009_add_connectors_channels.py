"""schema/v0009_add_connectors_channels.py — connectors, MCP servers, channels
tables (used by Phase 5 connectors + channels)."""
from schema.migrations import register


@register(9, "add connectors, mcp servers, channels tables")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS connectors (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            connected_at REAL NOT NULL
        );
    """)
    st.execute("""
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            command TEXT NOT NULL,
            declared_hosts TEXT NOT NULL DEFAULT '[]',
            capabilities TEXT NOT NULL DEFAULT '[]',
            read_only INTEGER NOT NULL DEFAULT 1,
            connected_at REAL NOT NULL
        );
    """)
    st.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            token TEXT NOT NULL DEFAULT '',
            allowed_user_ids TEXT,
            configured_at REAL NOT NULL
        );
    """)
