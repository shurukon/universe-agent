"""schema/v0001_initial.py — initial schema: planets, asteroids, stars, skills,
orbits, providers, audit, rules, handoffs, checkpoints."""
from __future__ import annotations
from schema.migrations import register


@register(1, "initial schema")
def upgrade(st) -> None:
    # Planets (L1 working memory)
    st.execute("""
        CREATE TABLE IF NOT EXISTS planets (
            id TEXT PRIMARY KEY,
            goal_text TEXT NOT NULL,
            goal_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            session_context TEXT NOT NULL DEFAULT '{}',
            solar_system_id TEXT,
            owner_session_id TEXT
        );
    """)
    # Moons (L1 sub-tasks)
    st.execute("""
        CREATE TABLE IF NOT EXISTS moons (
            id TEXT PRIMARY KEY,
            planet_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            session_context TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (planet_id) REFERENCES planets(id) ON DELETE CASCADE
        );
    """)
    # Asteroids (L2 episodic)
    st.execute("""
        CREATE TABLE IF NOT EXISTS asteroids (
            id TEXT PRIMARY KEY,
            goal_id TEXT,
            planet_id TEXT,
            task_description TEXT NOT NULL,
            decisions TEXT NOT NULL DEFAULT '[]',
            obstacles TEXT NOT NULL DEFAULT '[]',
            outcomes TEXT NOT NULL DEFAULT '[]',
            gravity_score REAL NOT NULL DEFAULT 0.0,
            gravity_provenance TEXT NOT NULL DEFAULT '{}',
            fingerprint TEXT,
            fingerprint_hash TEXT,
            solar_system_id TEXT,
            category TEXT NOT NULL DEFAULT 'general',
            domain TEXT NOT NULL DEFAULT 'general',
            language TEXT NOT NULL DEFAULT 'en',
            task_success INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            promoted_to TEXT,
            owner_session_id TEXT,
            FOREIGN KEY (planet_id) REFERENCES planets(id) ON DELETE SET NULL
        );
    """)
    # Stars (L3 semantic cache)
    st.execute("""
        CREATE TABLE IF NOT EXISTS stars (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            domain TEXT NOT NULL DEFAULT 'general',
            summary TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            vault_path TEXT,
            interaction_count INTEGER NOT NULL DEFAULT 0,
            last_used REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            edge_count INTEGER NOT NULL DEFAULT 0,
            owner_session_id TEXT
        );
    """)
    # Star edges (knowledge graph)
    st.execute("""
        CREATE TABLE IF NOT EXISTS star_edges (
            src TEXT NOT NULL,
            dst TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            kind TEXT NOT NULL DEFAULT 'related',
            PRIMARY KEY (src, dst)
        );
    """)
    # Skills (L4 procedural)
    st.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT '1.0.0',
            description TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            triggers TEXT NOT NULL DEFAULT '[]',
            license TEXT NOT NULL DEFAULT 'MIT',
            confidence REAL NOT NULL DEFAULT 0.9,
            status TEXT NOT NULL DEFAULT 'trusted',
            signature TEXT,
            category TEXT NOT NULL DEFAULT 'general',
            target_agent TEXT,
            last_used REAL NOT NULL DEFAULT 0,
            last_verified REAL NOT NULL DEFAULT 0,
            use_count INTEGER NOT NULL DEFAULT 0,
            needs_review INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            UNIQUE(name, source)
        );
    """)
    # Skill activations (audit / confidence decay)
    st.execute("""
        CREATE TABLE IF NOT EXISTS skill_activations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            skill_id TEXT NOT NULL,
            agent TEXT,
            goal_id TEXT,
            outcome TEXT NOT NULL DEFAULT 'success'
        );
    """)
    # Dark Matter (L5 meta)
    st.execute("""
        CREATE TABLE IF NOT EXISTS dark_matter (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    # Task success flags (windowed error_rate)
    st.execute("""
        CREATE TABLE IF NOT EXISTS task_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            domain TEXT NOT NULL,
            success INTEGER NOT NULL,
            goal_id TEXT
        );
    """)
    # Orbits
    st.execute("""
        CREATE TABLE IF NOT EXISTS orbits (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            solar_system_id TEXT,
            data TEXT NOT NULL DEFAULT '{}',
            updated_at REAL NOT NULL
        );
    """)
    # Rules (Black/White/Worm holes)
    st.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            rule TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'global',
            created_at REAL NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'user'
        );
    """)
    # Providers + keys
    st.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            name TEXT PRIMARY KEY,
            base_url TEXT NOT NULL
        );
    """)
    st.execute("""
        CREATE TABLE IF NOT EXISTS provider_keys (
            provider TEXT NOT NULL,
            id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'high',
            encrypted_secret TEXT NOT NULL DEFAULT '',
            tier TEXT NOT NULL DEFAULT 'paid',
            PRIMARY KEY (provider, id),
            FOREIGN KEY (provider) REFERENCES providers(name) ON DELETE CASCADE
        );
    """)
    st.execute("""
        CREATE TABLE IF NOT EXISTS agent_models (
            agent TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL
        );
    """)
    st.execute("""
        CREATE TABLE IF NOT EXISTS agent_fallbacks (
            agent TEXT NOT NULL,
            position INTEGER NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            PRIMARY KEY (agent, position)
        );
    """)
    # Handoff chain
    st.execute("""
        CREATE TABLE IF NOT EXISTS handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id TEXT NOT NULL,
            step INTEGER NOT NULL,
            from_agent TEXT NOT NULL,
            to_agent TEXT,
            package TEXT NOT NULL,
            ts REAL NOT NULL
        );
    """)
    # Capability grants (saved consents — White Holes)
    st.execute("""
        CREATE TABLE IF NOT EXISTS capability_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            capability TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'global',
            goal_id TEXT,
            granted_at REAL NOT NULL,
            expires_at REAL
        );
    """)
