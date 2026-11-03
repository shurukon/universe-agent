"""v21: skills.enabled — the Settings → Skills toggle (shell parity).

A skill the user switched off stays in the corpus (durable source of
truth, signatures intact) but is excluded from every L4 search so agents
never see it. DEFAULT 1 keeps every existing row active; the Settings
page is the only writer. (v20 is taken by feedback.)
"""
from schema.migrations import register


@register(21, "skills enabled flag (Settings toggle)")
def upgrade(st) -> None:
    try:
        st.execute(
            "ALTER TABLE skills ADD COLUMN enabled "
            "INTEGER NOT NULL DEFAULT 1;"
        )
    except Exception:
        pass  # column already present (restored backup / re-run)
