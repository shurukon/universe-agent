"""v23: hot-path indexes for the queries the desktop bridge hits on every
sidebar refresh / memory snapshot (2026-10 audit, MEDIUM).

Verified column names against the real queries before writing:
  - core/memory/layers/l1_working.py:119  planets WHERE goal_id=? ORDER BY created_at DESC
  - core/memory/layers/l1_working.py:158  moons  WHERE planet_id=? ORDER BY created_at
  - universe_desktop_bridge/app.py:1678     handoffs WHERE goal_id=?  (delete)
  - universe_desktop_bridge/memory_view.py:41  skill_activations GROUP BY skill_id
  - universe_desktop_bridge/app.py:885-898  goals WHERE deleted=0 AND kind='task'
                                          AND archived=0 ... ORDER BY pinned DESC, created_at DESC

planets/moons/handoffs/skill_activations are defined in v0001 (columns
goal_id / planet_id / skill_id all TEXT NOT NULL); goals gets deleted+kind
+archived+pinned from v0014/v0017/v0022 ALTERs. All creations are
IF NOT EXISTS so a re-run is a no-op, and every index is a cheap covering
key for the bridge's LIMIT queries.
"""
from schema.migrations import register


@register(23, "hot-path indexes: planets/moons/handoffs/skill_activations/goals sidebar")
def upgrade(st) -> None:
    st.execute(
        "CREATE INDEX IF NOT EXISTS idx_planets_goal ON planets(goal_id);")
    st.execute(
        "CREATE INDEX IF NOT EXISTS idx_moons_planet ON moons(planet_id);")
    st.execute(
        "CREATE INDEX IF NOT EXISTS idx_handoffs_goal ON handoffs(goal_id);")
    st.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_activations_skill "
        "ON skill_activations(skill_id);")
    # the sidebar query's exact predicate+order: equality on deleted/kind/
    # archived, then the pinned DESC / created_at DESC sort
    st.execute(
        "CREATE INDEX IF NOT EXISTS idx_goals_sidebar "
        "ON goals(deleted, kind, archived, pinned, created_at);")
