"""v15: consent persistence — capability_grants gains the tool name.

The in-process grant memory (goal_id, tool_name) used to die with the
process, so every restart re-prompted the same per_goal consents. Grants
are now written to the capability_grants table (tool column added) and
reloaded at gate construction."""
from schema.migrations import register


@register(15, "capability_grants.tool for persistent consents")
def upgrade(st) -> None:
    st.execute(
        "ALTER TABLE capability_grants ADD COLUMN tool TEXT NOT NULL DEFAULT '';"
    )


# v14 note: the downgrade() convention was dropped — migrations are
# forward-only (the module docstring in migrations.py already says so).
