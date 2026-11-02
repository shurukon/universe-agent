"""v24: the one agent's stored key migrates galaxy -> universe.

The full project rename (Galaxy -> Universe Agent, 2026-10-28) changed
every code default in one sweep, so FRESH databases already carry
'universe' rows and this migration is a no-op for them. But databases
created before the rename (including the live desktop DB, which sits at
v23 with agent values 'galaxy') still key the doer by the old name in:

1. agent_models / agent_fallbacks — the (provider, model) routing rows.
   The v22 mapping (general -> galaxy at the time) brought them to
   'galaxy'; this brings them to the final name 'universe'. Providers
   and models are preserved verbatim — only the key changes.
2. skill_agent_ownership — which skills belong to the doer.
3. skills.target_agent — search-time ownership; l4_procedural joins on
   it, so a stale 'galaxy' value would silently hide every owned skill
   from the doer's recall.

Loud schema preconditions (the v0022 pattern): each touched table must
exist and carry the 'agent'/'target_agent' column, checked via PRAGMA
table_info; anything else raises with table+column context instead of
silently bumping user_version. The chain guarantees v0001/v0010/v0012
ran first — a missing table there is real drift.

Historical note: timeline event rows keep agent='galaxy' verbatim —
they are the record of what ran under the old name, not something to
rewrite.
"""
from schema.migrations import register


def _cols(st, table: str) -> list[str]:
    return [r["name"] for r in st.query_all(f"PRAGMA table_info({table});")]


def _require(st, table: str, column: str) -> None:
    """Loud schema precondition: table must exist and carry the column."""
    cols = _cols(st, table)
    if column not in cols:
        raise RuntimeError(
            f"v24 migration precondition failed: table {table!r} "
            f"({'missing' if not cols else 'present'}) has no column "
            f"{column!r} — refusing to migrate silently")


@register(24, "agent key rename galaxy -> universe (full product rename)")
def upgrade(st) -> None:
    for table in ("agent_models", "agent_fallbacks"):
        _require(st, table, "agent")
        st.execute(
            f"UPDATE {table} SET agent='universe' WHERE agent='galaxy';")
    _require(st, "skill_agent_ownership", "agent")
    st.execute(
        "UPDATE skill_agent_ownership SET agent='universe' "
        "WHERE agent='galaxy';")
    _require(st, "skills", "target_agent")
    st.execute(
        "UPDATE skills SET target_agent='universe' "
        "WHERE target_agent='galaxy';")
