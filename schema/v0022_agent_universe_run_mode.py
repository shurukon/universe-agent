"""v22: the one agent is named universe + goal run_mode + no synthetic project.

Three deletes of the word "general" from runtime truth:

1. agent identity: the doer formerly named general is `universe` (the user's
   standing order: the agent is named "universe", not "general" — general the
   AGENT ceases to exist). agent_models / agent_fallbacks / skill
   ownership rows carry the old key; this migrates every one to `universe`
   in place, preserving each agent's configured provider/model.
2. goal run_mode: per-task marker ('task' = a normal professional agent
   turn, 'goal' = the /goal long-horizon mode — plan first, work for
   hours without stopping). The desktop bridge writes it per task; the
   engine reads nothing from it (the annex rides the request). DEFAULT
   'task' keeps every historical/CLI row a plain task.
3. the synthetic project row v14 inserted ('general','General') is gone.
   Its goals are re-parented to the oldest real project (empty string =
   unassigned when none exists); the UI offers "Select project" instead
   of inventing a fake project.

Audit 2026-10: every statement used to sit in `except Exception: pass`,
so a schema-drift failure silently "succeeded" and bumped user_version.
Now each step verifies the table/column it touches via PRAGMA table_info
(the v0017 pattern) and raises loudly with table+column context; the
runner's transaction + backup-restore then treat the run as failed.
Absent tables are NOT tolerated: the migration chain guarantees v0001/
v0004/v0012/v0014 ran before v22 — a missing table there is real drift.
The one legitimate re-entry (run_mode already added) is detected with the
same PRAGMA check instead of a blanket except.
"""
from schema.migrations import register


def _cols(st, table: str) -> list[str]:
    return [r["name"] for r in st.query_all(f"PRAGMA table_info({table});")]


def _require(st, table: str, column: str) -> None:
    """Loud schema precondition: table must exist and carry the column."""
    cols = _cols(st, table)
    if column not in cols:
        raise RuntimeError(
            f"v22 migration precondition failed: table {table!r} "
            f"({'missing' if not cols else 'present'}) has no column "
            f"{column!r} — refusing to migrate silently")


@register(22, "agent universe rename + goals.run_mode + no synthetic project")
def upgrade(st) -> None:
    # the (provider, model) configured for the doer follows the rename:
    # the settings page pins ONE row per agent, keyed by name.
    for table in ("agent_models", "agent_fallbacks"):
        _require(st, table, "agent")
        st.execute(
            f"UPDATE {table} SET agent='universe' WHERE agent='general';")
    _require(st, "skill_agent_ownership", "agent")
    st.execute(
        "UPDATE skill_agent_ownership SET agent='universe' "
        "WHERE agent='general';")
    # skills owned by the doer follow the rename: search-time ownership
    # (l4_procedural.join on skills.target_agent) must keep finding them
    _require(st, "skills", "target_agent")
    st.execute(
        "UPDATE skills SET target_agent='universe' "
        "WHERE target_agent='general';")

    # idempotent re-entry (restored backup / re-run): the column is
    # detected by PRAGMA, not by a swallow-everything except.
    if "run_mode" not in _cols(st, "goals"):
        _require(st, "goals", "text")  # table sanity before the ALTER
        st.execute(
            "ALTER TABLE goals ADD COLUMN run_mode TEXT NOT NULL "
            "DEFAULT 'task';")

    # retire the synthetic project: first re-parent its goals to the
    # oldest REAL project (id != 'general', oldest row wins), then drop
    # the row itself. Goals keep '' (unassigned) when no real project
    # exists — the UI groups them under an explicit "Select project" slot.
    _require(st, "goals", "project_id")
    _require(st, "projects", "id")
    st.execute(
        "UPDATE goals SET project_id = COALESCE("
        "(SELECT id FROM projects WHERE id != 'general' "
        "ORDER BY created_at LIMIT 1), '') "
        "WHERE project_id = 'general';")
    st.execute("DELETE FROM projects WHERE id = 'general';")
