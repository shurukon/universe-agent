"""Persist explicit agent -> provider key assignments."""
from schema.migrations import register


@register(10, "add per-agent provider key assignments")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS agent_provider_keys (
            agent TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            key_id INTEGER NOT NULL,
            FOREIGN KEY (provider, key_id)
                REFERENCES provider_keys(provider, id)
                ON DELETE CASCADE
        );
    """)


def downgrade(st) -> None:
    st.execute("DROP TABLE IF EXISTS agent_provider_keys;")
