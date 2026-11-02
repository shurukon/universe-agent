"""schema/v0007_add_skill_quarantine.py — community skills land in a quarantine
queue awaiting per-skill user approval (§18)."""
from schema.migrations import register


@register(7, "add skill quarantine queue")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS skill_quarantine (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_url TEXT,
            normalized TEXT NOT NULL,
            signature TEXT,
            submitted_at REAL NOT NULL,
            approved INTEGER NOT NULL DEFAULT 0,
            reviewed_at REAL
        );
    """)
