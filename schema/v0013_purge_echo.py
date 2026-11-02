"""Purge every persisted 'Universe Echo' row.

The in-process template provider is gone; any rows it left behind (agent
models, fallback chains, provider registry entries from the old use-echo
endpoint) must not survive it. This migration is idempotent.
"""
from schema.migrations import register


@register(13, "purge legacy Universe Echo provider rows")
def upgrade(st) -> None:
    st.execute("DELETE FROM agent_models WHERE provider = 'Universe Echo';")
    st.execute("DELETE FROM agent_fallbacks WHERE provider = 'Universe Echo';")
    st.execute("DELETE FROM agent_provider_keys WHERE provider = 'Universe Echo';")
    st.execute("DELETE FROM provider_keys WHERE provider = 'Universe Echo';")
    st.execute("DELETE FROM providers WHERE name = 'Universe Echo';")


def downgrade(st) -> None:
    pass  # forward-only purge; nothing to restore
