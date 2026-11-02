"""v0027 — sessions + checkpoints for ZCode harness."""
from __future__ import annotations
from schema.migrations import register
from storage.local import LocalStorage
@register(27, "sessions + checkpoints (ZCode harness)")
def upgrade(st: LocalStorage) -> None:
    st.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, workspace_root TEXT DEFAULT '', title TEXT DEFAULT 'New session', created_at REAL NOT NULL, updated_at REAL NOT NULL);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);")
    st.execute("CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), seq_snapshot TEXT DEFAULT '', fs_snapshot_ref TEXT DEFAULT '', created_at REAL NOT NULL);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id);")
