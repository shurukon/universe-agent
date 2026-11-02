"""schema/v0008_add_privacy_tier.py — per-solar-system privacy level (§17):
Public | Personal | Sensitive | Ephemeral."""
from schema.migrations import register


@register(8, "add privacy tier")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS solar_systems (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            privacy_tier TEXT NOT NULL DEFAULT 'personal',
            created_at REAL NOT NULL,
            owner_session_id TEXT
        );
    """)
    st.execute("CREATE INDEX IF NOT EXISTS idx_asteroids_goal ON asteroids(goal_id);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_asteroids_cat ON asteroids(category, domain);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_stars_domain ON stars(domain);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_domain ON task_outcomes(domain, ts);")
