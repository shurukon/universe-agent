"""Skill taxonomy, durable solar-system/orbit registry, and agent ownership."""
from schema.migrations import register


@register(12, "add durable skill taxonomy and solar-system registry")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS skill_solar_systems (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            trust_level TEXT NOT NULL DEFAULT 'bundled_trusted',
            taxonomy_version TEXT NOT NULL DEFAULT '2026.08.1',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    st.execute("""
        CREATE TABLE IF NOT EXISTS skill_orbits (
            id TEXT PRIMARY KEY,
            solar_system_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(solar_system_id, name),
            FOREIGN KEY (solar_system_id) REFERENCES skill_solar_systems(id) ON DELETE CASCADE
        );
    """)
    st.execute("""
        CREATE TABLE IF NOT EXISTS skill_agent_ownership (
            agent TEXT NOT NULL,
            solar_system_id TEXT NOT NULL,
            orbit_id TEXT,
            ownership TEXT NOT NULL DEFAULT 'primary',
            created_at REAL NOT NULL,
            PRIMARY KEY (agent, solar_system_id, orbit_id),
            FOREIGN KEY (solar_system_id) REFERENCES skill_solar_systems(id) ON DELETE CASCADE,
            FOREIGN KEY (orbit_id) REFERENCES skill_orbits(id) ON DELETE CASCADE
        );
    """)
    st.execute("""
        CREATE TABLE IF NOT EXISTS skill_taxonomy_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_id TEXT NOT NULL,
            taxonomy_version TEXT NOT NULL,
            solar_system_id TEXT NOT NULL,
            orbit_id TEXT NOT NULL,
            agent TEXT,
            confidence REAL NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            needs_review INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
    """)
    for column, definition in (
        ("solar_system_id", "TEXT"),
        ("orbit_id", "TEXT"),
        ("taxonomy_version", "TEXT NOT NULL DEFAULT ''"),
        ("taxonomy_confidence", "REAL NOT NULL DEFAULT 0.0"),
        ("taxonomy_reason", "TEXT NOT NULL DEFAULT ''"),
        ("taxonomy_needs_review", "INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            st.execute(f"ALTER TABLE skills ADD COLUMN {column} {definition};")
        except Exception:
            pass
    st.execute("CREATE INDEX IF NOT EXISTS idx_skills_taxonomy ON skills(solar_system_id, orbit_id, taxonomy_confidence);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_skills_agent_taxonomy ON skills(target_agent, solar_system_id, orbit_id);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_skill_orbits_system ON skill_orbits(solar_system_id, name);")
    st.execute("CREATE INDEX IF NOT EXISTS idx_skill_taxonomy_audit_skill ON skill_taxonomy_audit(skill_id, created_at);")


def downgrade(st) -> None:
    st.execute("DROP TABLE IF EXISTS skill_taxonomy_audit;")
    st.execute("DROP TABLE IF EXISTS skill_agent_ownership;")
    st.execute("DROP TABLE IF EXISTS skill_orbits;")
    st.execute("DROP TABLE IF EXISTS skill_solar_systems;")
    # Keep skill columns for backward-compatible downgrade safety.
