"""v25: the goal run_mode is retired — one turn engine runs every task.

The goal-era split (user order 2026-10-28): NOT every task is a goal. The
'goal' run mode (/goal long-horizon variant with its own budget and annex)
is deleted; the ZCode turn engine (core/agent/turn_engine.py) runs every
task with the same unbounded loop, and task identity no longer carries a
mode marker. goals.run_mode is dropped from the schema; the historical
values ('task' on every row since the column's default never let anything
else persist meaningfully) are not preserved — the column carried no
information the engine ever read.

SQLite DROP COLUMN (3.35+) with a PRAGMA-guarded no-op on re-entry: a
fresh database never carries the column permanently (v0022 adds it,
v0025 drops it again) — the pair is self-cancelling for new installs,
while upgraded DBs lose the column here.

Loud schema preconditions (the v0022 pattern): the goals table must exist;
anything else raises with table context instead of silently bumping
user_version.
"""
from schema.migrations import register


def _cols(st, table: str) -> list[str]:
    return [r["name"] for r in st.query_all(f"PRAGMA table_info({table});")]


@register(25, "retire goals.run_mode — the goal era is over")
def upgrade(st) -> None:
    cols = _cols(st, "goals")
    if not cols:
        raise RuntimeError(
            "v25 migration precondition failed: table 'goals' is missing "
            "— refusing to migrate silently")
    if "run_mode" in cols:
        st.execute("ALTER TABLE goals DROP COLUMN run_mode;")
