"""v19: automations — the scheduled-goal table (ZCode Automations parity).

One row per automation: a 5-field cron schedule, the prompt it fires,
its enabled flag, and the last/next run timestamps. The schedule/
package ticks every 30 s (never a long sleep — a sleeping process can
miss its window after an OS sleep/wake; a tick re-reads the clock each
cycle, so the kozum setTimeout-overflow class of bug cannot exist here).

Timestamps are unix-epoch REAL, matching goals.created_at.
"""
from schema.migrations import register


@register(19, "automations table (scheduled goals)")
def upgrade(st) -> None:
    st.execute(
        """
        CREATE TABLE IF NOT EXISTS automations (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            schedule    TEXT NOT NULL,      -- 5-field cron: m h dom mon dow
            prompt      TEXT NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            last_run_at REAL,
            next_run_at REAL,
            created_at  REAL NOT NULL
        );
        """
    )
    st.execute(
        "CREATE INDEX IF NOT EXISTS idx_automations_due "
        "ON automations(enabled, next_run_at);"
    )
