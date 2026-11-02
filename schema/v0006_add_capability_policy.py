"""schema/v0006_add_capability_policy.py — user-configurable capability policy
overrides (tightening/loosening the defaults from §4)."""
from schema.migrations import register


@register(6, "add capability policy overrides")
def upgrade(st) -> None:
    st.execute("""
        CREATE TABLE IF NOT EXISTS capability_policy (
            capability TEXT PRIMARY KEY,
            consent TEXT NOT NULL DEFAULT 'auto',
            updated_at REAL NOT NULL
        );
    """)
